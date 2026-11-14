"""Invented studio member sources: one reader, one key, and bounded callers."""

from __future__ import annotations

import bounds  # every invocation has a deadline

import json
import threading
import time
from unittest import mock

from memory_forest import groups
from memory_forest.config import AgentGroup, load_config
from memory_forest.errors import MemoryForestError

from test_v1_find import FILES
from test_v1_groups import BAKERY_GROUPS
from v1_support import ForestTestCase


class MemberRefreshTests(ForestTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.source = "fictional-member-reader"
        self.group = AgentGroup(id="source-room", source=self.source)
        settings = BAKERY_GROUPS + '\n[agents.groups.source-room]\nmembers = []\nsource = "' + self.source + '"\n'
        self.root = self.make_forest({**FILES, "forest.toml": settings})
        self.addCleanup(groups.unregister_member_source, self.source)
        self.addCleanup(groups._SOURCE_CACHE.clear)

    def _register(self, reader, *, cooperative=False):
        groups.register_member_source(self.source, reader, cooperative=cooperative)

    def _read(self, **kwargs):
        return groups._read_source(self.root, self.group, **kwargs)

    def _failure(self, call):
        with self.assertRaises(MemoryForestError) as caught:
            call()
        self.assertEqual((caught.exception.code, caught.exception.next_step), ("member_source_failed", "report"))
        return caught.exception

    def _thread(self, call, answers):
        def work():
            try:
                answers.append(call())
            except BaseException as exc:
                answers.append(exc)

        thread = threading.Thread(target=work)
        thread.start()
        self.addCleanup(thread.join, 3)
        return thread

    def test_source_entries_are_names_by_key_with_first_spelling_and_duplicate_diagnostics(self):
        self._register(lambda _group: ["Crumb Scout", "CRUMB_SCOUT", "Ｃｒｕｍｂ—Ｓｃｏｕｔ", "Crumb.Scout"])
        self.assertEqual(self._read(), ("Crumb Scout", "Crumb.Scout"))
        for spelling in ("crumb-scout", "CRUMB_SCOUT", "Ｃｒｕｍｂ Ｓｃｏｕｔ"):
            self.assertTrue(groups._on_source(spelling, self._read()))
            self.assertEqual(groups.resolve_caller(self.root, self.group.id, spelling).id, "crumb-scout")
        self.assertFalse(groups._on_source("Crumb+Scout", self._read()))
        row = next(row for row in groups.listing(self.root)["groups"] if row["id"] == self.group.id)
        self.assertEqual(row["source"], self.source)
        self.assertFalse(row["members"][0]["lane"])
        self.assertEqual(len(row["left_out"]), 2)
        self.assertTrue(all("duplicate" in value and "Crumb Scout" in value for value in row["left_out"]))
        self.assertIn("duplicate", "\n".join(groups.listing_lines(groups.listing(self.root))))

    def test_every_non_list_or_non_text_entry_is_malformed_without_admission(self):
        values = ["Crumb", 7, {"Crumb": True}, ("Crumb",), None, ["Crumb", 7], [None], [True],
                  (name for name in ["Crumb"])]
        before = (self.root / "forest.toml").read_bytes()
        for value in values:
            with self.subTest(value=type(value).__name__):
                groups._SOURCE_CACHE.clear()
                self._register(lambda _group: value)
                failure = self._failure(lambda: groups.resolve_caller(self.root, self.group.id, "C"))
                self.assertIn("malformed result", failure.message)
                self.assertEqual((self.root / "forest.toml").read_bytes(), before)

    def test_source_removal_revokes_admitted_membership_but_never_main_access(self):
        listed, calls = [["Crumb Scout", "CRUMB_SCOUT"]], []

        def reader(group):
            calls.append(group)
            if listed[0] is None:
                raise RuntimeError("fictional source unavailable")
            return listed[0]

        self._register(reader)
        with mock.patch.object(groups, "SOURCE_INTERVAL_SECONDS", 0):
            admitted = groups.resolve_caller(self.root, self.group.id, "crumb-scout")
            self.assertEqual(admitted.id, "crumb-scout")
            self.assertEqual(load_config(self.root).agents.declared(admitted.id).name, "Crumb Scout")
            before = (self.root / "forest.toml").read_bytes()
            listed[0] = []
            with self.assertRaises(MemoryForestError) as caught:
                groups.resolve_caller(self.root, self.group.id, "CRUMB_SCOUT")
            self.assertEqual(caught.exception.code, "agent_not_in_group")
            self.assertEqual((self.root / "forest.toml").read_bytes(), before)
            listed[0] = None
            self._failure(lambda: groups.resolve_caller(self.root, self.group.id, admitted.id))
            reads_before_main = len(calls)
            self.assertEqual(groups.resolve_caller(self.root, self.group.id, "front-desk").id, "front-desk")
            self.assertEqual(len(calls), reads_before_main)
            listed[0] = ["CRUMB_SCOUT"]
            returned = groups.resolve_caller(self.root, self.group.id, admitted.id)
            self.assertEqual(returned.id, admitted.id)
            self.assertFalse(returned.wrote)

    def test_source_id_references_use_global_identity_and_do_not_replace_a_stored_name(self):
        from memory_forest import move
        move.execute(self.root, "new-agent", id="crate-bot", name="Crate.Bot", today="2051-03-02")
        self._register(lambda _group: ["crate-bot"])
        result = groups.resolve_caller(self.root, self.group.id, "crate-bot")
        self.assertEqual(result.id, "crate-bot")
        self.assertEqual(load_config(self.root).agents.declared("crate-bot").name, "Crate.Bot")
        self.assertEqual(groups.resolve_caller(self.root, self.group.id, "CRATE.BOT").id, "crate-bot")
        with self.assertRaises(MemoryForestError) as caught:
            groups.resolve_caller(self.root, self.group.id, "Crate Bot")
        self.assertEqual(caught.exception.code, "agent_not_in_group")
        with (self.root / "forest.toml").open("a") as stream:
            stream.write('\n[agents.known.shadow]\nname = "crate-bot"\n')
        with self.assertRaises(MemoryForestError) as caught:
            groups.resolve_caller(self.root, self.group.id, "shadow")
        self.assertEqual(caught.exception.code, "agent_not_in_group")
        groups._SOURCE_CACHE.clear()
        self._register(lambda _group: ["shadow"])
        self.assertEqual(groups.resolve_caller(self.root, self.group.id, "shadow").id, "shadow")
        for spelling in ("Crate Bot", "crate_bot"):
            self.assertEqual(groups.resolve_caller(self.root, self.group.id, spelling).id, "shadow")
        with self.assertRaises(MemoryForestError) as caught:
            groups.resolve_caller(self.root, self.group.id, "crate-bot")
        self.assertEqual(caught.exception.code, "agent_not_in_group")

    def test_size_is_bounded_in_names_and_characters_before_copying(self):
        self.assertEqual(groups._call_source(self.group, ["★" * 200])[0], ("★" * 200,))
        self.assertEqual(groups._call_source(self.group, [f"Crumb {n}" for n in range(50_000)])[0][-1], "Crumb 49999")
        for items, reason in ((["Crumb"] * 50_001, "more than 50000"), (["★" * 201], "longer than 200")):
            failure = self._failure(lambda: groups._call_source(self.group, items))
            self.assertIn(reason, failure.message)
        kept, left = groups._call_source(self.group, ["Crumb", "", "\nCrumb", "Crumb\t", "Shelf\nBot"])
        self.assertEqual(kept, ("Crumb",))
        self.assertEqual(len(left), 4)

    def test_reader_exceptions_and_start_failure_are_source_failures_without_a_stranded_slot(self):
        def raises(_group):
            raise MemoryForestError("agent_not_in_group", "fictional adapter refusal")

        self._register(raises)
        self.assertIn("MemoryForestError", self._failure(self._read).message)
        groups._SOURCE_CACHE.clear()
        for fault in (RuntimeError("fictional thread failure"), OSError("fictional thread failure")):
            groups._SOURCE_CACHE.clear()
            with mock.patch.object(threading.Thread, "start", side_effect=fault):
                failure = self._failure(self._read)
                self.assertIn("could not start", failure.message)
                self.assertIn(type(fault).__name__, failure.message)
            diagnostics = groups.source_problems(self.root, load_config(self.root))
            self.assertEqual(len(diagnostics), 1)
            self.assertIn("could not start", diagnostics[0][1])
            self.assertIn(type(fault).__name__, diagnostics[0][1])
            with groups._SOURCE_GUARD:
                self.assertNotIn(self.source, groups._SOURCE_ACTIVE)
        groups._SOURCE_CACHE.clear()
        self._register(lambda _group: ["Crumb Scout"])
        self.assertEqual(self._read(), ("Crumb Scout",))

    def test_a_fault_in_the_returned_list_releases_the_source_slot(self):
        class BrokenList(list):
            def __len__(self):
                raise RuntimeError("fictional malformed list")

        self._register(lambda _group: BrokenList())
        failure = self._failure(lambda: self._read(deadline=time.monotonic() + 0.1))
        self.assertIn("failed while validating its result (RuntimeError)", failure.message)
        with groups._SOURCE_GUARD:
            self.assertNotIn(self.source, groups._SOURCE_ACTIVE)
        groups._SOURCE_CACHE.clear()
        self._register(lambda _group: ["Crumb Scout"])
        self.assertEqual(self._read(), ("Crumb Scout",))

    def test_check_reports_the_last_failure_only_for_the_current_source_without_starting_it(self):
        config = load_config(self.root)
        self.assertEqual(groups.source_problems(self.root, config), [])
        row = next(row for row in groups.listing(self.root)["groups"] if row["id"] == "night-shift")
        self.assertNotIn("source_failed", row)
        self._failure(self._read)
        problems = groups.source_problems(self.root, config)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0][0], self.group.id)
        self.assertIn("Last member-source read", problems[0][1])
        self.assertIn("has no reader", problems[0][1])
        row = next(row for row in groups.listing(self.root)["groups"] if row["id"] == self.group.id)
        self.assertIn("has no reader", row["source_failed"])
        from memory_forest.checks import run_checks
        _checks, reported = run_checks(self.root, config, names=["agents"])
        self.assertTrue(any(problem.code == "member_source_failed" and self.group.id in problem.detail
                            for problem in reported), reported)
        state_path = self.root / groups.SOURCE_STATE
        for entry in (None, [], {"source": "old-fictional-reader", "failed": "old failure"},
                      {"source": self.source}, {"source": self.source, "failed": ""}):
            state_path.write_text(json.dumps({self.group.id: entry}), encoding="utf-8")
            self.assertEqual(groups.source_problems(self.root, config), [])
            row = next(row for row in groups.listing(self.root)["groups"] if row["id"] == self.group.id)
            self.assertNotIn("source_failed", row)
        state_path.write_text(json.dumps({"night-shift": {"source": "", "failed": "fictional failure"}}),
                              encoding="utf-8")
        self.assertEqual(groups.source_problems(self.root, config), [])
        row = next(row for row in groups.listing(self.root)["groups"] if row["id"] == "night-shift")
        self.assertNotIn("source_failed", row)

    def test_six_concurrent_misses_share_success_and_failure(self):
        for failing in (False, True):
            with self.subTest(failing=failing):
                groups._SOURCE_CACHE.clear()
                entered, release, joined = threading.Event(), threading.Event(), threading.Event()
                self.addCleanup(release.set)
                calls, answers, waits = [], [], []

                def reader(group):
                    calls.append(group)
                    entered.set()
                    if not release.wait(2):
                        raise RuntimeError("test release did not arrive")
                    if failing:
                        raise RuntimeError("fictional source unavailable")
                    return ["Crumb Scout"]

                self._register(reader)
                threads = [self._thread(self._read, answers)]
                self.assertTrue(entered.wait(1))
                with groups._SOURCE_GUARD:
                    read = groups._SOURCE_ACTIVE[self.source]
                original_wait = read.done.wait

                def observe_wait(timeout=None):
                    waits.append(True)
                    if len(waits) >= 5:
                        joined.set()
                    return original_wait(timeout)

                with mock.patch.object(read.done, "wait", observe_wait):
                    threads += [self._thread(self._read, answers) for _ in range(5)]
                    self.assertTrue(joined.wait(1))
                    release.set()
                    for thread in threads:
                        thread.join(2)
                        self.assertFalse(thread.is_alive())
                self.assertEqual(calls, [self.group.id])
                self.assertEqual(len(answers), 6)
                if failing:
                    self.assertTrue(all(isinstance(answer, MemoryForestError) for answer in answers))
                    self.assertEqual({answer.code for answer in answers}, {"member_source_failed"})
                    self.assertEqual(len({answer.message for answer in answers}), 1)
                else:
                    self.assertEqual(answers, [("Crumb Scout",)] * 6)

    def test_a_short_joining_caller_has_its_own_deadline_without_cancelling_other_callers(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        tokens, answers = [], []

        def reader(_group, *, deadline, cancelled):
            tokens.append((deadline, cancelled))
            entered.set()
            release.wait(2)
            return ["Crumb Scout"]

        self._register(reader, cooperative=True)
        deadline = time.monotonic() + 2
        thread = self._thread(lambda: self._read(deadline=deadline), answers)
        self.assertTrue(entered.wait(1))
        start = time.monotonic()
        failure = self._failure(lambda: self._read(deadline=start + 0.03))
        self.assertIn("this caller's deadline", failure.message)
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertFalse(tokens[0][1].is_set())
        self.assertEqual(tokens[0][0], deadline)
        release.set()
        thread.join(2)
        self.assertEqual(answers, [("Crumb Scout",)])

    def test_a_hung_reader_keeps_one_slot_across_groups_forests_and_reregistration(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []

        def reader(group):
            calls.append(group)
            entered.set()
            release.wait(3)
            return ["Crumb Scout"]

        self._register(reader)
        started = time.monotonic()
        self._failure(lambda: self._read(deadline=started + 0.03))
        self.assertTrue(entered.is_set())
        workers = [thread for thread in threading.enumerate() if thread.name == f"member-source-{self.source}"]
        self.assertEqual(len(workers), 1)
        problems = groups.source_problems(self.root, load_config(self.root))
        self.assertEqual(len(problems), 1)
        self.assertIn("has not come back", problems[0][1])
        self.assertIn("started at", problems[0][1])
        other = self.make_forest({**FILES, "forest.toml": BAKERY_GROUPS}, name="other-forest")
        other_group = AgentGroup(id="other-room", source=self.source)
        groups.unregister_member_source(self.source)
        self._register(lambda _group: ["Replacement"])
        start = time.monotonic()
        for _ in range(12):
            failure = self._failure(self._read)
            self.assertIn("has not come back", failure.message)
            self._failure(lambda: groups._read_source(other, other_group))
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertEqual(calls, [self.group.id])
        self.assertTrue(workers[0].is_alive())
        release.set()
        workers[0].join(1)
        self.assertFalse(workers[0].is_alive())
        self.assertIn("deadline", self._failure(self._read).message)

    def test_cooperative_reader_observes_cancellation_and_late_success_is_discarded(self):
        cancelled_seen = threading.Event()
        tokens = []

        def reader(_group, *, deadline, cancelled):
            tokens.append(cancelled)
            if cancelled.wait(1):
                cancelled_seen.set()
            return ["Crumb Scout"]

        self._register(reader, cooperative=True)
        self._failure(lambda: self._read(deadline=time.monotonic() + 0.03))
        self.assertTrue(cancelled_seen.wait(1))
        with groups._SOURCE_CHANGED:
            self.assertTrue(groups._SOURCE_CHANGED.wait_for(lambda: self.source not in groups._SOURCE_ACTIVE, 1))
        self.assertTrue(tokens[0].is_set())
        self.assertIn("deadline", self._failure(self._read).message)
        self.assertEqual(len(tokens), 1)

    def test_interval_begins_at_completion_with_an_injected_clock_for_success_and_failure(self):
        for failing in (False, True):
            with self.subTest(failing=failing):
                groups._SOURCE_CACHE.clear()
                now, calls = [100.0], []

                def reader(_group):
                    calls.append(now[0])
                    now[0] += 4
                    if failing:
                        raise RuntimeError("fictional source unavailable")
                    return ["Crumb Scout"]

                self._register(reader)

                def call():
                    if failing:
                        self._failure(lambda: self._read(clock=lambda: now[0]))
                    else:
                        self.assertEqual(self._read(clock=lambda: now[0]), ("Crumb Scout",))

                call()
                now[0] = 163.9
                call()
                self.assertEqual(calls, [100.0])
                now[0] = 164.0
                call()
                self.assertEqual(calls, [100.0, 164.0])

    def test_completion_at_the_deadline_is_not_a_success_and_a_backwards_clock_invalidates_cache(self):
        now = [100.0]

        def late(_group):
            now[0] += groups.SOURCE_SECONDS
            return ["Crumb Scout"]

        self._register(late)
        self.assertIn("deadline", self._failure(lambda: self._read(clock=lambda: now[0])).message)
        calls = []
        self._register(lambda group: calls.append(group) or ["Shelf Scout"])
        now[0] = 99.0
        self.assertEqual(self._read(clock=lambda: now[0]), ("Shelf Scout",))
        self.assertEqual(calls, [self.group.id])

    def test_a_different_group_waits_for_the_source_slot_but_never_reuses_another_groups_result(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        answers, calls = [], []

        def reader(group):
            calls.append(group)
            if group == self.group.id:
                entered.set()
                release.wait(2)
            return [group]

        self._register(reader)
        first = self._thread(self._read, answers)
        self.assertTrue(entered.wait(1))
        other = AgentGroup(id="other-room", source=self.source)
        self._failure(lambda: groups._read_source(self.root, other, deadline=time.monotonic() + 0.03))
        self.assertEqual(calls, [self.group.id])
        release.set()
        first.join(2)
        self.assertEqual(answers, [(self.group.id,)])
        self.assertEqual(groups._read_source(self.root, other), (other.id,))
        self.assertEqual(calls, [self.group.id, other.id])

