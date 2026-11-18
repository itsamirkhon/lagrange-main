# MONOREPO — решения и статус

## Исходные репозитории (импорт с историей через `git subtree add`)

| Источник | Ветка / SHA | Префикс (этап 1) | Итоговое место |
|---|---|---|---|
| itsamirkhon/lagrange-ontology | main `016a862` | `_import/ontology` | `src/lagrange_rx/{ontology,ingest,ai}`, `rules/`, `config/`, `docs/` |
| itsamirkhon/lagrange-enigine | main `7d030cc` | `_import/engine` | `src/lagrange_rx/engine`, `eval/`, `scripts/` |
| itsamirkhon/langrange-sim | main `2726515` | `_import/sim` | `sim/` |
| ibrohim-b/lagrange-rx | main `4d92cea` | `_import/ibrohim` | `web/` (только интерфейс) |

Ветка `feat/twin-compliance-core` (`0772f25`) намеренно не импортирована: её двойник заменён ядром из lagrange-enigine.

## Этап 1 — импорт без изменений кода

Тесты каждого источника «как есть» в своём префиксе (Python 3.14, venv, `PYTHONPATH=src:.`):

| Префикс | Результат |
|---|---|
| `_import/ontology` | 184 passed |
| `_import/engine` | зелёные (оракул, мутации, AST) |
| `_import/sim` | 75 passed (7 мин: генерация миров) |
| `_import/ibrohim` | 37 passed |

## Этап 2 — один пакет, одни зависимости, одни правила

Соответствие «источник → куда перенесено» (все переносы `git mv`, история сохранена):

| Источник | Куда |
|---|---|
| ontology `src/lagrange_rx/{ontology,ingest,ai,pnl.py}` | `src/lagrange_rx/…` (без изменений) |
| ontology `api.py` | `src/lagrange_rx/api/ingest.py` |
| engine `engine/*`, `fixtures/` | `src/lagrange_rx/engine/`, `src/lagrange_rx/fixtures/` |
| engine `api/app.py` | `src/lagrange_rx/api/engine.py` |
| engine `rules.py` | `src/lagrange_rx/engine/rules.py` — теперь тонкий адаптер над единым каталогом |
| engine `ontology.py` | `src/lagrange_rx/engine/_legacy_ontology.py` — **временно**, удаляется на этапе 3 |
| engine `eval/*`, ontology `eval/*` | `eval/` (корень) |
| engine `tests/*`, ontology `tests/*`, sim `tests/*` | `tests/{engine,eval,ontology,ingest,sim}/` |
| sim `sim/` | `sim/` |
| ibrohim `web/*` | `web/` (UI, этап 9); `docs/PRICING_3735.md`, `docs/CONTRACT_V2.md`, концепт — `docs/`; остальное — `docs/prototype_ibrohim/` |
| ibrohim `src/*`, `tests/*` (одноэтапный MILP) | удалено (история сохранена); заменено ядром engine |

Решения:
- **Каталог правил** — `rules/uz_pharma_rules.yaml` (17 правил, статусы сохранены, комментарий про машинный перевод №3735 сохранён). Добавлены параметры ядра:
  `P20.lag_days=30` (assumption), `P22.p_cut/depth_min/depth_max` (`assumption: true`), `UZ-INV-LEGAL-VS-ECONOMIC-BASE.hedge_forward_premium=0.02` (`assumption: true`).
  Число правил осталось 17. Формат каталога — формат онтологии (список); `engine/rules.py` только переупаковывает числа по `rule_id`.
- Эвал-скрипты лежат в корневом `eval/` (пакет), импорт `eval.engine_eval`.
- Три `api.py` → `api/ingest.py`, `api/engine.py` (объединяются в этапе 7).
- Зависимости: один `pyproject.toml`, extras `[ingest] [engine] [sim] [dev]`.

Тесты: `pytest --ignore=tests/sim` → 430 passed (ontology+ingest+engine+eval); `tests/test_boundaries.py` — AST-граница продукт/сим, пути истины, единственный каталог правил
(тест «нет дублирующих моделей» пока `xfail` до этапа 3).
