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
