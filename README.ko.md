# Agent Memory Forest

**오래 일하는 AI agent와 agent 팀을 위한, 사람이 읽을 수 있고 규칙으로 관리되는 로컬 메모리입니다.**

[English](README.md) · **한국어** · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Français](README.fr.md) · [Deutsch](README.de.md)

[![CI](https://github.com/hyungchulc/agent-memory-forest/actions/workflows/ci.yml/badge.svg)](https://github.com/hyungchulc/agent-memory-forest/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-2EA44F.svg)](LICENSE)
[![Local-first](https://img.shields.io/badge/runtime-local--first%20%7C%20no%20network-555555.svg)](docs/privacy-and-trust.md)
[![GitHub stars](https://img.shields.io/github/stars/hyungchulc/agent-memory-forest?style=social)](https://github.com/hyungchulc/agent-memory-forest/stargazers)

⭐ Agent Memory Forest가 쓸모 있었다면 GitHub에서 star를 눌러 주세요. 다른 사람들이 찾는 데 도움이 됩니다.

https://github.com/user-attachments/assets/a45d205e-bedd-4887-903d-bd44253ddd80

Agent Memory Forest는 agent가 알게 된 것을 내 디스크의 번호 붙은 몇 개 폴더 안에 평범한 Markdown 페이지로
남기는 Python 패키지이자 `forest` 명령입니다. 사실마다 집이 하나 있고, 그 집은 사실을 가져온 대화나 agent가
아니라 규칙이 정합니다. 페이지가 어디에 있어야 하는지, 어떤 subject가 자기 페이지를 가질 만큼 자랐는지는
model이 아니라 코드가 판단합니다.

**현재 상태:** 버전 `1.0.0b1`, beta입니다. 버전 1의 첫 공개 배포입니다. 0.3의 `memory-forest` 명령도
나란히 설치된 채로 남아 있습니다([archive](docs/archive-0.3/cli.md)).

> [!IMPORTANT]
> Agent Memory Forest는 hosted service도, 권한 시스템도, agent가 정확히 기억한다는 보장도 아닙니다. 메모리는
> 판단의 근거일 뿐입니다. 호출하는 쪽이 안내받은 페이지를 직접 열어 얼마나 최신인지 확인하고, 자기 권한과
> 안전 규칙을 따로 적용해야 합니다. forest는 credential을 보관하지 않으며, 선택 사항인 vector 검색을 빼면
> 모든 기능이 network 없이 동작합니다.

## 여기서 시작하세요

Python 3.11 이상과 POSIX filesystem이 필요하며, 표준 라이브러리 밖의 runtime dependency는 없습니다.
Windows에서는 WSL2를 쓰고 forest를 Linux filesystem 안에 두세요. 먼저 [WSL2 guide](docs/wsl2.md)를 읽어
주세요. 패키지는 PyPI에 있습니다. 이번 배포는 beta이므로, 버전을 지정하거나 `--pre`를 붙여 설치합니다.

```sh
python -m pip install "agent-memory-forest==1.0.0b1"
# 버전을 지정하지 않을 때:
python -m pip install --pre agent-memory-forest
```

clone을 받아 설치하려면 그 폴더에서 `python -m pip install .`을 실행합니다. 설치는 `forest --version`으로
확인합니다. `forest` 명령이 path에 없으면 `python -m memory_forest.forest`가 같은 명령입니다. 그다음 내
agent에게 한 문장만 말하면 됩니다.

> `forest guide`를 실행하고 그대로 따라 해 줘.

그러면 agent가 스스로 연결합니다. `forest setup`을 실행하면 주인에게 물어볼 질문들이 나오고, agent는 그
질문을 하나씩 주인에게 묻습니다. forest를 어디에 둘지, agent의 이름, 주인의 main agent인지, 어떤 대화를
들여올지, 누가 forest를 키울지입니다. 주인이 답해야 하는 질문은 모두 주인에게 가고, agent가 대신 답하는
일은 없습니다. 이어서 agent는 forest를 새로 만들거나 주인이 고른 forest에 들어가고, 찾기 server를 자기
도구에 등록하고, 자기 대화를 들여온 뒤, `forest doctor`로 결과를 증명합니다.

## 들어 있는 것

- **forest와 그 규칙.** 번호 붙은 폴더에 담긴 평범한 페이지, 그리고 모든 forest가 지키는 contract 하나:
  [contract](docs/contract.md). 처음 읽는 사람을 위한 설명은 [concepts](docs/concepts.md)에 있습니다.
- **구조 도구.** `forest init`, `registry`, `move`, `levels`, `collections`, `record`, `archive`,
  `check`, `lint`, `numbers`, `credentials`, `recover`: [tools](docs/tools.md).
- **찾기.** 명령으로는 `forest search`, `read`, `timeline`, `navigate`가 있고, `forest mcp`는 도구 다섯
  개(`memory_search`, `memory_read`, `memory_timeline`, `memory_navigate`, `memory_log`)를 가진 MCP
  server입니다: [tools](docs/tools.md#forest-mcp).
- **쓰기 입구 둘.** agent가 자기 턴을 쓰는 `forest log`와 `memory_log`, 그리고 lane 모양의 기록을 읽는
  `forest intake --from jsonl`: [tools](docs/tools.md#forest-intake).
- **agent마다 tree 하나.** 모두에게 필요한 것은 공유 tree에, agent 자신의 것은 그 agent의 tree에 두고,
  읽기 범위로 나눕니다: [agents](docs/multi-agent.md).
- **guide, setup, doctor.** `forest guide`(setup, routine, use, contract), `forest setup`, `forest doctor`:
  [agent 연결하기](docs/setup.md), [tools](docs/tools.md#forest-guide).

## 하지 않는 것

- **특정 agent 프로그램의 대화 기록은 읽지 않습니다.** 프로그램마다 대화를 자기 형식으로 남기고, 패키지가
  그 형식을 모두 따라갈 수는 없습니다. 몇 개만 골라 reader를 넣으면 지킬 수 없는 약속을 하는 셈입니다. 자기
  프로그램은 agent가 압니다. agent는 guide를 따라 자기 턴을 기록으로 덧붙이는 작은 exporter를 직접
  씁니다. 어떤 프로그램을 잘 아는 사람은 그 프로그램의 reader를 만들 수 있습니다([adapters](docs/adapters.md)).
- **엔진도 타이머도 없습니다.** daily 기록, 정리 실행, 관리 실행은 주인의 agent가 `forest guide routine`을
  따라 자기 일정과 자기 model로 합니다. 패키지는 판정을 맡습니다: `forest check`, `forest lint`,
  `forest levels`, `forest doctor`.
- **다른 프로그램의 설정에는 아무것도 쓰지 않고**, plugin도 없습니다. setup은 forest 폴더 안에만 쓰고,
  자기 도구에 무엇을 등록할지는 agent에게 알려 줍니다.

## 직접 하기

agent 없이 하면 같은 과정이 명령 몇 줄입니다. 답 없이 setup을 실행하면 질문 목록이 나오고, 답을 모두 주면
forest를 쓰고 `todo`를 출력합니다.

```sh
forest setup                                   # next: ask_owner, 질문마다 그 flag와 함께
forest setup --root studio-forest --as "Kiln Bot" --main yes --conversations all --earlier no \
  --keeper this --when "every night at 23:30" --daily-model "small model" --filing-model "large model"
# next: done
# todo 1. register_mcp  2. bring_turns  3. own_turn  4. routine  5. verify, 각각 명령과 함께
```

`todo`를 그대로 합니다. MCP client에 `forest mcp --as kiln-bot --root studio-forest`를 등록하고, 출력된
`forest intake` 명령이나 `forest log`로 턴을 들여옵니다. 그다음 증명합니다.

```sh
forest doctor --as "Kiln Bot" --root studio-forest --phrase   # 말할 새 phrase를 출력
forest log --as kiln-bot --root studio-forest <<'TURN'
Kiln Two fires to cone 5 for now. forest doctor phrase orchid maple amber
TURN
forest index build --root studio-forest
forest doctor --as "Kiln Bot" --root studio-forest              # result: passed (exit 0) 또는 todo (exit 1)
```

phrase는 매번 새로 만들어지니, 내 doctor가 출력한 것을 들여오세요. `finding` 검사는 MCP client가 server를
한 번 호출해야 통과합니다. 그 전까지 doctor는 `todo`라고 답하고 등록할 `forest mcp` 명령을 보여 줍니다.
MCP client가 없으면 명령이 곧 입구입니다. doctor 줄에 `--doors commands`를 붙이면 doctor가 직접 실행한
검색이 증거가 됩니다.

주인의 답은 forest가 지킵니다. forest를 키우는 agent는 모든 agent의 턴을 읽어야 하므로 main agent여야
합니다(`--keeper this`에 `--main no`를 주면 다시 묻습니다). 그리고 어떤 agent에 `--conversations none`이라고
답하면, exporter나 hook이 아직 돌고 있어도 forest가 그 agent의 턴을 거절합니다(`recording_off`).

## 문서

| 문서 | 내용 |
| --- | --- |
| [Concepts](docs/concepts.md) | 처음 읽는 사람을 위한 설명: 계층, subject의 일생, 사실의 집, 위로 올라가는 것, 기록, 이동, lint |
| [Contract](docs/contract.md) | forest가 지켜야 할 모든 규칙을 한 번씩 |
| [agent 연결하기](docs/setup.md) | `forest setup`이 묻고 쓰는 것, `forest doctor`가 증명하는 것 |
| [Agents](docs/multi-agent.md) | agent tree, 두 가지 정리 방식, 읽기 범위, writer 하나 원칙 |
| [Design](docs/v1-design.md) | 패키지: 디스크 구조, 설정, package map, milestone |
| [Tools](docs/tools.md) | `forest move`, `levels`, `collections`, `check`, `lint`, `record`, `numbers`, `intake`, `index`, `search`, `timeline`, `log`, `mcp`, `guide`, `setup`, `doctor` |
| [reader 만들기](docs/adapters.md) | 어떤 agent 프로그램을 잘 아는 사람이 그 프로그램의 대화 기록을 읽는 reader를 만드는 법 |
| `forest guide` | setup guide: agent가 스스로 연결할 때 따르는 단계 |
| `forest guide routine` | routine guide: forest를 키우는 agent를 위한 daily 기록, 정리 실행, 관리 실행 |
| `forest guide use` | use guide: agent가 언제, 어떻게 찾아보는지 |
| `forest guide contract` | 설치된 패키지가 출력하는 contract |
| [Comparison](docs/comparison.md) | Mem0, GBrain, LLM Wiki 아이디어, 일반 vector RAG와 무엇이 다른지 |
| [Privacy and trust](docs/privacy-and-trust.md) | 무엇이 로컬에 남는지, 읽는 쪽이 무엇을 볼 수 있는지, 신뢰할 수 없는 데이터로서의 메모리 |
| [Roadmap](docs/roadmap.md) | 세 milestone과 나중에 올 수도 있는 것 |
| [WSL2](docs/wsl2.md) | Windows에서 WSL2로 쓰는 법 |
| [0.3 archive](docs/archive-0.3/) | 버전 1이 대체한 0.3 문서 |

guide 셋과 contract는 패키지 안(`src/memory_forest/guide/`)에 들어 있고 위 명령이 출력합니다. 그래서 agent는 자기가
설치한 버전에 맞는 guide를 읽습니다. 문서는 영어로 쓰여 있습니다.

## 왜 만들었나

오래 일하는 agent의 메모리는 대개 둘 중 하나로 무너집니다. 너무 적게 남기거나, 아무것도 찾을 수 없고 아무도
고칠 수 없을 때까지 쌓이기만 합니다. 버전 1은 이 문제를 정면으로 다루는 몇 가지 원칙 위에 서 있습니다.

- **단위는 subject입니다.** 사람, 프로젝트, 기기, agent마다 별칭까지 담은 registry 한 줄이 있고, 그 자료는
  자기 자리에 모입니다.
- **자리는 코드가 정합니다.** subject가 공유 페이지의 한 항목인지, leaf인지, branch인지, tree인지는 그 자료가
  걸쳐 있는 서로 다른 날짜의 수로 정해집니다. 모든 이동은 참조를 고쳐 쓰고 기록을 남기는 하나의 도구를
  거칩니다.
- **사실마다 집은 하나입니다.** 다른 페이지에는 복사본 대신 날짜가 붙은 참조 한 줄만 둡니다. 그래서 같은
  사실이 여기저기서 어긋날 일이 없습니다.
- **이력은 남습니다.** 바뀐 사실은 그 페이지의 update record로 옮겨지고, 길어진 기록은 그대로 archive에
  보관되며, 지워지는 것은 없습니다.
- **위층에는 확실한 것만 올라갑니다.** 취향이나 습관은 여러 날에 걸친 근거가 있거나 주인이 직접 말했을 때만
  요약 페이지에 올라갑니다. 가장 최근에 나온 말이라는 이유만으로는 올라가지 않습니다.
- **코드로 검사하는 lint가 있습니다.** 모양이 있는 규칙은 모두 검사합니다. 한 번의 실행은 자기가 만든 문제를
  스스로 정리해야 하지만, 원래 있던 문제 때문에 막히지는 않습니다.
- **여러 agent를 위한 구조입니다.** agent별 raw lane, 한 번에 하나뿐인 writer, 읽기 범위, 두 가지 정리
  방식을 갖추고 있습니다.

## 한눈에 보는 forest

```mermaid
flowchart TB
    owner(["주인"]) <--> hub["Orchestrator agent"]
    hub <--> botA["Bot A"]
    hub <--> botB["Bot B"]

    subgraph lanes ["Lane (forest 아님)"]
        istm["06 istm<br/>agent마다 하나,<br/>덧붙이기만 하는<br/>원본 기록"]
        daily["05 daily<br/>하루 한 페이지,<br/>읽을 수 있는 기록"]
        istm --> daily
    end

    hub --> istm
    botA --> istm
    botB --> istm

    daily --> filing(["정리 실행<br/>한 번에<br/>writer 하나"])
    filing -- leaf로 --> forest

    subgraph forest ["Forest"]
        xltm["01 xltm<br/>정체성<br/>tree 목록<br/>agent 목록"]
        subgraph own ["agent tree"]
            altm["02 ltm<br/>tree 페이지"] --> amtm["03 mtm<br/>index와 board"] --> astm["04 stm<br/>leaf"]
        end
        subgraph shared ["공유 tree"]
            ltm["02 ltm<br/>tree 페이지"] --> mtm["03 mtm<br/>index와 board"] --> stm["04 stm<br/>leaf"]
        end
        xltm --> ltm
        xltm --> altm
    end

    hub -. 전체를 읽음 .-> forest
    botA -. 공유 tree와 자기 tree를 읽음 .-> forest
    botB -. 공유 tree와 자기 tree를 읽음 .-> forest
```

자료는 lane에서 leaf로 올라가고, leaf가 뒷받침하는 것만 board와 tree 페이지로 올라갑니다. 자리를 정할 때는
반대로 위에서 내려옵니다. root index가 어떤 종류의 subject가 어느 tree에 속하는지 정하고, tree가 branch를,
branch가 leaf를 정합니다. agent의 tree도 다른 tree와 같은 세 계층을 가집니다. 다른 점은 생기는 때(subject가
자란 뒤가 아니라 agent의 첫날), 담는 것(기본값에서는 agent 자신의 것, 곧 역할, 지시, 방법, 교훈, 실행 기록),
그리고 읽는 쪽입니다. 하루 한 번 스친 이름이 자기 tree를 갖게 되기까지의 과정은
[Concepts](docs/concepts.md)에서 한 subject를 따라가며 설명합니다.

| 폴더 | 담는 것 |
| --- | --- |
| `01 xltm/XLTM.md` | root index: 주인이 누구인지, tree 목록과 배치 규칙, agent 목록 |
| `02 ltm/<tree>_LTM.md` | tree마다 한 페이지: 범위, 오래 가는 패턴과 규칙, 현재 상태, branch |
| `03 mtm/<tree>/` | tree의 index(subject registry)와 branch마다 하나씩 있는 board |
| `04 stm/<tree>/<branch>/` | leaf: subject 하나의 하위 주제 하나를 원문 수준으로, 본문 8 KB 이내 |
| `05 daily/` | 하루하루의 읽을 수 있는 기록 (lane) |
| `06 istm/` | 덧붙이기만 하는 원본 기록, agent마다 하나 (lane) |
| `.forest/` | 컴파일된 registry, 이동 기록, archive, index, lock |

## 찾기를 다섯 줄로

forest에서 무언가가 필요한 agent는 MCP server인 `forest mcp`로 묻거나, 같은 입구를 명령으로 씁니다. 어떤
도구를 부를지는 질문에 무엇이 들어 있는지에 달려 있습니다. 돌아오는 것은 결론이 아니라 읽을 자리입니다.
페이지, 섹션, 줄 번호, 그리고 왜 걸렸는지입니다.

```mermaid
flowchart TB
    agent(["Agent"]) -- "누가 읽는지는 연결이 정함" --> door["forest mcp<br/>또는 같은 입구인<br/>명령"]
    door -- "이름, 물건, 단어 몇 개" --> s["memory_search<br/>이름과 별칭 먼저<br/>그다음 단어"]
    door -- "어떤 기간" --> t["memory_timeline<br/>아직 정리되지 않은<br/>시간까지"]
    door -- "어디를 봐야 할지 모를 때" --> n["memory_navigate<br/>root index에서<br/>한 단계씩 아래로"]
    s --> idx[("index<br/>페이지에서 만들고<br/>다시 만들 수 있음")]
    t --> idx
    idx --> r["읽는 쪽 범위 안의<br/>읽을 자리<br/>페이지 · 섹션 · 줄<br/>걸린 이유"]
    n --> r
    r --> rd["memory_read<br/>적힌 그대로의<br/>페이지"]
```

가상의 [synthetic forest](examples/synthetic-forest/README.md)를 복사해 두고, 그 폴더 안에서 실행한 예입니다.

```sh
forest search "the big kiln" --as kiln-bot --limit 1       # subject의 이름이 아니라 별칭
# 03 mtm/studio/kiln-two.md
#   why: alias: "the big kiln" names kiln-two
#   ...
#   03 mtm/studio/kiln-two.md#rules lines 18-21
#     ## Rules
#
#     - Glaze loads fire to cone 5 until the new elements have run ten firings; Juno's decision of 2042-05-14 (as of 2042-05-14; source `04 stm/studio/kiln-two/settings.md`)
#   ...
# trees: studio
forest read "04 stm/studio/kiln-two/settings.md" --as kiln-bot --lines 14-15   # 그 줄이 출처로 든 leaf
# 04 stm/studio/kiln-two/settings.md
#   why: read
#   04 stm/studio/kiln-two/settings.md lines 14-15
#     - Glaze loads: cone 5 (1,196 °C) until the new elements have run ten firings. Juno, in the studio chat:
#       "Let's drop glaze loads to cone 5 until the new elements settle, ten firings or so."
#
# trees: studio
forest timeline query "2042-05-13" --as kiln-bot           # 그날 하루: leaf, daily 페이지, raw lane
forest navigate --as kiln-bot                              # 찾을 말이 없을 때: root index에서 시작
forest read "02 ltm/kiln-bot_LTM.md" --as ledger-bot       # 다른 agent의 tree
# forest: out_of_scope: That path is outside this reader's trees and lane.
#   path: 02 ltm/kiln-bot_LTM.md
#   next: fix
```

1. 입구는 둘이고 답은 하나입니다. `forest mcp --as <agent>`는 MCP client에 `memory_search`, `memory_read`,
   `memory_timeline`, `memory_navigate`, `memory_log`를 stdio나 loopback port로 제공합니다.
   `forest search`, `read`, `timeline`, `navigate`, `log`는 같은 입구를 명령으로 쓰는 것이고, 범위도 답도
   같습니다.
2. index는 페이지에서 만들어 낸 것입니다. `forest index build`가 페이지와 lane을 읽어 index를 쓰고(reset이 새
   파일을 가리키기 전까지는 `.forest/index.sqlite`), routine은 단계를 마칠 때마다 이 명령을 실행하며,
   `forest index reset` 뒤에 다시 build하면 같은 index가 나옵니다. 검색은 build 뒤에 바뀐 lane 파일과 daily
   페이지부터 들여오므로, 아직 정리되지 않은 시간도 찾을 수 있습니다. tree의 페이지가 바뀐 것은 다음 build
   때 들어옵니다.
3. 이름이 단어보다 먼저입니다. 검색은 registry의 이름과 별칭부터 봅니다. 질문에 어떤 subject의 이름이
   나오면 그 subject의 집과, `about:`으로 그 subject를 가리키는 모든 leaf가 나옵니다. 단어 일치는 그다음이고,
   vector 일치는 설정에 embeddings provider의 이름이 있고 그 provider를 직접 등록해 두었을 때만 더해집니다.
   패키지는 아무것도 등록하지 않으며, 밖으로 나가는 network 호출을 하지 않습니다.
4. 결과는 읽을 자리를 가리킬 뿐입니다. 페이지마다 결과가 하나씩 나오고, 걸린 섹션이 적힌 그대로, 왜
   걸렸는지와 읽을 줄 번호와 함께 나옵니다. agent는 그 페이지를 열어 날짜와 update record를 확인하고 자기
   규칙을 적용합니다. 메모리는 어떤 것도 허가하지 않습니다.
5. 연결이 곧 읽는 쪽입니다. 누가 읽는지는 `--as`가 정하고, 도구의 인자로도 질문의 표현으로도 바꿀 수
   없습니다. group이 함께 쓰는 연결(`--as-group`)에서는 호출마다 그 멤버 가운데 자기 agent의 이름을 댑니다.
   orchestrator는 forest 전체와 모든 raw lane을 읽습니다. 다른 agent는 기본값으로 공유 tree와 자기 tree,
   root index, daily 페이지, 자기 raw lane을 읽으며, 그 밖의 경로는 거절됩니다. forest가 알아보는 로그인
   값과 카드·계좌·전화번호는 index에서도, 모든 답에서도 가려집니다.

자세한 내용은 [Tools](docs/tools.md#forest-search-and-forest-read)에 있고, agent 자신은 `forest guide use`를 읽습니다.

## agent를 다섯 줄로

여러 agent가 하나의 forest를 함께 씁니다. 모두에게 필요한 것은 공유 tree에, 각 agent 자신의 것은 agent마다
하나씩 있는 tree에 두고, orchestrator는 전체를 읽습니다. `forest setup`으로 들어온 agent는 setup이 `done`을
답할 때 이미 자기 tree가 있습니다. 다른 agent의 tree는 저절로 생깁니다. agent가 자기 raw lane에 첫 줄을
쓰면, 다음 관리 단계가 그 tree를 만듭니다.

```sh
ls "my-forest/06 istm/post-bot"            # agent가 여기에 첫 줄을 썼습니다
# 2042-05-15.jsonl
forest levels apply --root my-forest       # 명령 하나: 모든 subject의 단계를 정하는 바로 그 관리 단계
# post-bot: none -> tree (agent; its raw lane holds a file)
ls my-forest/"02 ltm"
# post-bot_LTM.md   studio_LTM.md          # tree 페이지: 역할, 지시, 교훈, 현재 상태, branch
```

1. agent(orchestrator, bot, 예약 실행 단계)는 첫날부터 tree인 subject입니다. agent가 `forest log`나
   `memory_log`로 쓴 턴이 그 tree를 만들고, 다른 writer가 lock을 잡고 있으면 lock이 풀린 뒤의 다음 write가
   만들도록 pending으로 남깁니다. agent는 곧 그 이름이며, 어떤 문자로 쓴 이름이든 됩니다. forest 전체에서
   정확한 저장 id가 이름 key보다 먼저이고, 둘 다 group 멤버 확인보다 먼저입니다. 이름은 페이지 문법이
   필요로 하는 곳에서만 인코딩하며, 한 번 받아들인 이름을 또 다른 이름 규칙으로 거절하는 일은 없습니다.
   설정 항목이나 intake lane 때문에 만들어야 하는데 아직 없는 tree는 `forest levels apply`가 만듭니다.
2. agent의 tree 페이지에는 역할, 고정 지시, 교훈, 현재 상태, branch가 있습니다.
3. 기본값인 `subject-first`는 사실을 그 subject의 집에 두고 `noted by <agent>`라고 표시합니다.
   `agent-first`는 agent의 tree에 두고 `about:`으로 가리키다가, 두 agent가 같은 사실을 알게 되면 공유합니다.
4. orchestrator는 forest 전체를 읽고, 다른 agent는 공유 tree와 자기 tree를 읽습니다.
5. agent마다 자기 raw lane에 덧붙이기만 하고, forest에는 lock을 잡은 정리 실행 하나만 씁니다.

lane에 commit되는 순간 `stored`와 turn id가 확정됩니다. id, caller key와 source 기록, tree, index, connection 기록은 거기서 파생되며, 아직 끝나지 않은 것은 `pending`과 그 이유로 표시됩니다.

crash로 응답을 받지 못하면 호출한 쪽은 turn이 저장됐는지 알 수 없습니다. 같은 key로 호출을 다시 해도 안전합니다.

자세한 내용은 [Agents](docs/multi-agent.md)에 있습니다.

## 명령 한눈에 보기

명령은 `--root`를 주면 그 폴더를 쓰고, 아니면 현재 폴더와 그 위쪽에서 `forest.toml`이 있는 가장 가까운
폴더를 씁니다. forest를 만드는 명령은 `forest init`과 `forest setup`이고, `forest guide`는 forest가
없어도 됩니다. 아래는 forest를 만든 뒤 그 폴더로 들어가서, 이어지는 줄이 그 파일을 찾게 합니다.

```sh
# Milestone 1: 뼈대와 구조 도구
forest init my-forest                          # 설정, 계층, root index
cd my-forest                                   # 아래 명령은 이 폴더의 forest를 씁니다
forest move new-agent studio-hub --name "Studio Hub" --role "orchestrator"   # 또는 lane이 만들게 둡니다
forest registry build                          # 모든 ## Subjects 섹션을 컴파일
forest levels check                            # 단계가 바뀔 때가 된 subject와 agent
forest levels apply                            # 이동 도구로 실제로 옮기고 기록
forest check --sync                            # 파생 목록을 페이지에 맞춘 뒤 모든 검사
forest lint                                    # 모양이 있는 모든 규칙을 code로 검사
forest repeats check                           # 한 subject의 두 leaf가 함께 가진 사실 줄
forest record kinds                            # update record의 줄을 종류별로
forest numbers check --all                     # 가리지 않고 다 적힌 계좌·카드·신분증·전화번호

# Milestone 2: 연결하고 찾기
forest setup --root . --as "Kiln Bot" --main yes --conversations all --earlier no --keeper nobody
                                               # 주인의 답: agent, 그 tree, 그 기록 파일
forest intake --from jsonl --as kiln-bot --transcript transcripts/kiln-bot.jsonl --once
                                               # exporter가 덧붙인 기록을 그 agent의 raw lane으로
forest intake status                           # 어댑터가 알아보지 못한 줄의 수
forest log --as kiln-bot                       # 턴은 stdin으로
forest index build                             # .forest/index.sqlite, 지우고 다시 만들 수 있음
forest search "cone 5" --as kiln-bot           # 별칭을 먼저, 그다음 단어, 읽는 쪽의 범위 안에서
forest timeline query "yesterday evening" --as kiln-bot
forest navigate --as kiln-bot                  # root index에서 한 단계 아래로
forest mcp --as kiln-bot                       # search, read, timeline, navigate, memory_log
forest agents                                  # 그룹, lane, 연결에서 마지막으로 보인 때
forest guide                                   # setup guide. 나머지는 `routine`, `use`, `contract`
forest setup                                   # 답 없이: agent가 주인에게 물을 질문 (exit 1)
forest doctor --as kiln-bot                    # 턴이 들어오고 MCP server가 호출됐는지 증명 (둘 다 되기 전에는 exit 1)

# Milestone 3: 자라기. daily 기록, 정리 실행, 관리 실행은 주인의 agent가 `forest guide routine`을
# 따라 직접 하고, 위 명령들이 그 결과를 판정합니다.
```

코드가 돌기 전에 forest가 어떻게 생겼는지 보고 싶다면 가상의 [synthetic forest](examples/synthetic-forest/README.md)를
읽어 보세요. 주인 한 명, 공유 tree 셋, orchestrator와 bot 둘, 이틀치 daily 페이지와 raw lane까지 작은 파일
마흔 개 남짓으로 되어 있습니다.

## 현재 상태

Agent Memory Forest는 beta 단계입니다. 이 트리의 버전은 1.0.0b1이고, 버전 1의 첫 공개 배포입니다.

- **작성 완료:** contract, 설계, concepts와 agents 안내서, contract를 따르는 가상의 synthetic forest.
- **Milestone 1, 뼈대:** `forest` 구조 명령(registry, move, levels, collections, history 파일을 쓰는
  record, archive, `check`, `lint`, 번호 가리기, `recover`), lane이나 설정에서 만들어지는 agent tree, 모든
  commit의 되돌리기 journal. 가상 forest로 하는 테스트가 있고, synthetic forest는 모든 검사를 통과합니다.
- **Milestone 2, 연결하고 찾기:** `forest intake`가 lane 모양의 기록(`jsonl` 어댑터, 또는 entry point
  group `memory_forest.adapters`로 꽂아 넣은 어댑터)을 그 agent의 raw lane으로 받고, `forest log`와
  `memory_log`는 agent가 자기 턴을 씁니다. `forest index`가 검색 index를 만듭니다(`forest index reset`이 새
  파일을 가리키기 전까지는 `.forest/index.sqlite`). `forest search`, `forest read`, `forest timeline`,
  `forest navigate`가 그 index에서 `agents.readable_trees` 범위로 답하고, `forest mcp`는 search, read,
  timeline, navigate, `memory_log`를 제공합니다. 주인의 agent는 `forest guide`, `forest setup`,
  `forest doctor`로 스스로 연결합니다([design 15장](docs/v1-design.md#15-connecting-agents)).
- **Milestone 3, 자라기:** daily 기록, 정리 실행, 야간 관리는 주인의 agent가 contract(10장)와
  `forest guide routine`을 따라 직접 합니다.
- **이번 배포에 들어가지 않는 것:** 특정 agent 프로그램의 대화 기록을 읽는 reader(프로그램을 아는 사람이
  만듭니다, [adapters](docs/adapters.md)), daily·정리·관리 단계를 돌리는 엔진.

0.3 코드(`memory-forest`)는 버전 1 옆에 그대로 남아 있습니다. 평가하고 응용해 보기에는 알맞지만, 사람이
지켜보지 않는 중요한 결정에 쓸 수준은 아닙니다.

## 기여

[CONTRIBUTING.md](CONTRIBUTING.md)를 읽어 주세요. fixture는 지어낸 내용으로만 만들고, 실제 forest는 Git 밖에
두고, pull request 전에 전체 검사를 돌려 주세요.

```sh
make check
```

보안 문제는 [SECURITY.md](SECURITY.md)를, 커뮤니티 참여는 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)를
따릅니다.

## License

[MIT License](LICENSE)로 공개합니다.
