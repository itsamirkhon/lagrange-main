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


## Этап 3 — ядро на настоящем `ontology.ClientGraph`

Сделано:
- `engine/_legacy_ontology.py` удалён. Единственная модель мира — `lagrange_rx.ontology.ClientGraph`. Тест `test_no_duplicate_domain_models` больше не `xfail`.
- Выбран путь «тонкий адаптер» (рефакторинг всех типов ядра слишком дорог и не нужен): `engine/adapter.py` — `to_calculation_graph(graph, EngineConfig)`.
  Порядок защит: `ClientGraph.for_calculation()` (ссылки, `extracted_unconfirmed` не проходит) → блок `unallocated` затрат (`UnallocatedCostError`) → отображение полей с явной ошибкой `AdapterError` при нехватке данных.
- `engine/inputs.py` — плоские числовые типы ядра (`EngineGraph`, `CalculationGraph`, `RouteSpec`, `CostSpec`, ...). Это не вторая онтология: имена отличаются, строятся только адаптером (в тестах движка — напрямую, для изоляции).
- Соответствия: потолки и Rn — `RegistryRecord` (с НДС; `chun_formula_incl_vat` по п.19 → предупреждение при расхождении >1%); маршруты — `Route`+`ProductionSite`+`Carrier`; цена Kc и валюта — `Contract` (действует на старт горизонта, не заменён), иначе `Quote`; пошлина и сборы — `CustomsEntry` (законная база маршрута совпадает с `CustomsEntry.legal_base_c_per_pack`, тест); затраты — `CostItem` (acquisition только через `shipment`→маршрут; ниже линии: `direct` по своему SKU, `shared_by_volume/revenue` — доля SKU по `SalesRecord` периода; окно 12 мес. до горизонта; `unallocated` блокирует); спрос — сумма `DemandForecast` по 12 месяцам горизонта; надёжность — `Supplier.reliability`; кредит — `CreditLine` + `PaymentTerms`; курс — последний `FxRate` до горизонта.
- Чего нет в онтологии → `EngineConfig` (явный объект, документирован): размер лота (по умолчанию 1000, помечено допущением в `notes`), экстренные маршруты, премия и лимит, штраф за недопоставку, дни запаса, цена контракта продажи, Pr, режим «море» (в онтологии нет), эталон калибровки (`ActualSpec`), пошлина/сборы маршрутов без растаможки. Каждая подстановка по умолчанию попадает в `CalculationGraph.notes` и в `warnings` результата.
- Расширение онтологии не потребовалось (онтология не менялась; тесты онтологии зелёные). В `inputs.RouteSpec.mode` добавлен `multimodal`.
- Фикстура L0 (`fixtures/l0.py`) пересобрана моделями онтологии (все сущности с `provenance=synthetic`; второй SKU нужен для доли общих затрат; неподтверждённый маршрут R-ROAD-X убран: неподтверждённое не может быть в графе расчёта, тест на это есть).
- `run_optimize(graph, config=...)`, `calculation_graph(graph, config)` принимают онтологический граф + `EngineConfig` (или уже готовый граф ядра). `api/engine.py` — только правка импорта (принимает `EngineGraph`); перевод API на онтологию — этап 7.
- Правило 15% на проданных упаковках: в `rules/uz_pharma_rules.yaml` остаётся `needs_legal_review` (числа не менялись, добавлены комментарий и пометка в описании), `Rules.needs_legal_review`, предупреждение в `EngineResult.warnings`.

Известные пробелы: суммирование месячных квантилей спроса в годовые предполагает полную корреляцию месяцев; `FxHedge` из графа не используется (премия хеджа — из каталога правил); периоды затрат только `YYYY-MM`; `per_uzs_financed`/`financing` исключаются (финансирование считает ядро).

## Этап 4 — реальный курс ЦБ

- `data/cbu_fx_monthly.csv` — побайтовая копия `sim/inputs/fx_cbu.csv` (официальный API ЦБ, USD/EUR/RUB, 2025-01..2026-10, 22 месяца). Это вход, не истина; продукт читает только `data/`, не `sim/`.
- `load_fx_history()` по умолчанию читает `data/cbu_fx_monthly.csv`; **откат на синтетику убран** (нет файла — ошибка). Формат экспорта ЦБ (`date,currency,rate_uzs,source`, несколько наблюдений в месяц) разбирается в месячный ряд по первому наблюдению месяца; каждая строка обязана ссылаться на cbu.uz.
- `MIN_HISTORY_MONTHS` снижен с 24 до 18 (реальная история короче запланированных 2020–2026; блочный бутстрап по 21 доходности — выбор честнее, чем синтетика; ограничение отмечено). Можно удлинить историю `scripts/fetch_cbu_fx.py` (нужна сеть).
- В отчёте `fx_source: "cbu.uz official"` (`FxHistory.source_label`); `data/fx_sample_SYNTHETIC.csv` — только для тестов (`tests/engine/conftest.py`, `tests/engine/test_scenarios_fx.py`).
- Тесты: умолчание — реальные данные ЦБ, а не синтетика; нет отката; сценарии L0 строятся на `cbu`; разбор экспорта.

Тесты этапов 3–4: `tests/engine tests/eval tests/ontology tests/ingest tests/test_boundaries.py` → 467 passed (≈7,5 мин): оракул перебора 50/50, 0 нарушений законности на 10 000 случайных случаях, мутационные тесты compliance, AST-независимость `compliance.py`, 28 тестов адаптера (по тесту на отображение поля), `test_no_duplicate_domain_models` без `xfail`.
