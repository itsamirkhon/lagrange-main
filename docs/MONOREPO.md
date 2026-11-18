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

## Этап 6a

Ingest собирает подтверждённый `ClientGraph` мира L0 (`sim/worlds/L0/documents/`, 72 документа) в оффлайне: без сети, OCR и ИИ.
Запуск: `PYTHONPATH=src:. python -m eval.e2e_ingest_L0` → `eval/ingest_graph_L0.json` (граф, журнал вопросов, счётчики, список неизвлечённого, сверка P&L; `snapshot_id` внутри).

**Что сделано** (новые модули `src/lagrange_rx/ingest/offline/`; существующий разбор/классификация и `money_checks.py`/`checks.py` не тронуты):
- `readers.py` — чтение папки (PDF-строки без водяного знака, CSV cp1251, XLSX, EML), структурная классификация по заголовкам/маркерам; картинки (10 файлов) оффлайн не читаются → `unknown` + замечание, не угадываются.
- `supply_docs.py`, `finance_docs.py`, `sales.py` — детерминированные парсеры: строки упаковочных листов инвойсов, реестр ГТД (PDF+XLSX), реестр партий, тарифы и КП перевозчиков, КП поставщиков (ячейки усечены и слипаются — разбор по регулярному выражению с проверкой числа строк), договоры, расходы D10 (английские статьи в русской таблице → категории), выписки D13, 1С-продажи, региональные сводки (4 языка, только для сверки с 1С).
- `build.py` — `build_draft(folder)`: связи только по номерам/названиям/посту: ГТД↔инвойс (номер инвойса), инвойс↔партии (упаковочный лист), партии↔продажи (серия), партии↔реестр склада (накладная), ГТД→маршрут (таможенный пост из тарифов). Сущности: Product, Region, Channel, Supplier, ProductionSite, Carrier, Route (на пару коридор/поставщик, наблюдаемую по отгрузкам; воздушный коридор без отгрузок — без площадки), Shipment, Lot, CustomsEntry, FxRate, Contract (+ допсоглашения), Quote (поставщиков и перевозчиков), RegistryRecord (D13, дубли PDF/CSV сведены), CostItem. Всё в статусе `extracted_unconfirmed`.
- `confirm.py` — `commit(draft, answers)`: в граф попадают только подтверждённые группы; затраты без ответа по атрибуции и допсоглашения без даты начала не попадают; висячие ссылки отсекаются с отчётом; результат проходит `for_calculation()`.
- Очередь вопросов: 9 `confirm_group` (по классам документов), 2 `cost_attribution` (метка «Kardiozan…» и «Общий портфель препаратов»: SKU и правило распределения — только ответом человека), 2 `contract_effective_from` (допсоглашения указывают лишь дату подписания).
- `eval/sim_human.py` — «симулированный человек»: единственный читатель `sim/truth`; сверяет сводку группы с «книгами» (расхождение → `reject`), отвечает по атрибуции и датам, логирует каждый ответ с основанием; число ответов = метрика нагрузки (L0: 13).
- Онтология (только обратно совместимо): поля, которых нет ни в одном документе, теперь могут быть `None` (Product.inn/dosage_form/manufacturer/registration_certificate; Route: production_site, incoterms, лимиты, активация, eligibility, p90, фрахт в UZS; Quote: product, даты, incoterms, MOQ, срок годности; Shipment.actual_freight_uzs; CustomsEntry.vat_uzs; PaymentTerms.customer_days). Добавлены `Route.freight_per_*_usd`, `Quote.route_code`, `CostItem.scope` (общий пул затрат делится только между SKU из `scope`). `pnl.py` учитывает `scope`.

**Решения и допущения (помечены в `field_provenance` как `assumption`)**
- `CustomsEntry.fees_uzs = «Себестоимость без НДС» (D7) − CIP − пошлина`: D7 не делит сборы и входящий фрахт, поэтому `legal_base_c_per_pack` здесь равна полной посадочной стоимости на упаковку (включает фрахт). Для сверки с калибровкой это верно; для юридической базы п.19 — завышение на долю фрахта, пока нет разбивки.
- CostItem: `driver='fixed'` (ежемесячный акт без формулы), `layer='below_line'`; пул «Общий портфель» ответом симулированного человека отнесён к не-фокусным SKU (`shared_by_volume`, `scope`); правило деления в истине не задано — выбран объём.
- `Contract.valid_from` = дата подписания, для допсоглашений уточняется вопросом. `RegistryRecord`: Rn в выписке в UZS ⇒ `rn_currency='UZS'`, `filing_fx_rate=1`.
- «100% аккредитив» читается как предоплата 100% (в договоре поставщика Словения).

**Результат L0 (`snapshot_id` в `eval/ingest_graph_L0.json`)** — 72 документа; граф: 7 товаров, 4 поставщика, 44 отгрузки/ГТД, 50 партий, 3694 продажи, 264 затраты, 24 КП, 6 договоров, 6 маршрутов, 3 записи реестра; все 44 инвойса связаны с ГТД, все 3694 продажи — с партией, региональные сводки сходятся с 1С (0 расхождений). Вопросов человеку 13, отклонённых групп 0, отброшенных сущностей 0.
Сверка P&L по `pnl.py` (окно 2025-11..2026-10, фокус-SKU) против `sim/truth/calibration_report.json`: маржа 2,4300% против 2,43% (расхождение 0,0000 п.п.), объём 149 250 = 149 250, выручка совпала до копейки, себестоимость −153 UZS (округление), операционные затраты совпали. Это сверка данных Ingest, не калибровка двойника (она — следующий этап).

**Не извлечено (точный список — `not_extracted` в JSON)**: содержимое 10 изображений (служебные записки D11, чаты перевозчиков, фото накладных — OCR недоступен оффлайн, ИИ-зрение не запускалось); МНН, форма, производитель, РУ товара; параметры кредита (лимит, ставка, дата доступности — в D10 только помесячные остатки и проценты); разбивка сборов и фрахта в ГТД и НДС; фактический фрахт отгрузки; incoterms, лимиты, мощность, стоимость активации, допуск и p90 по маршрутам; площадка для коридоров без отгрузок; надёжность поставщиков, `customer_days`; индексации реестра, склады, прогнозы, хеджи; договоры перевозчиков (CNT-CAR-*: модель `Contract` только про поставщиков); НДС и дата выпуска КП поставщиков (дата — только у КП из заголовка).

**Тесты**: `tests/ingest` + `tests/ontology` + `tests/test_boundaries.py` — 234 passed, 1 xfailed (ожидаемый); `tests/eval` — 4 passed. Новые: `tests/ingest/test_{ontology_unknowns,pnl_scope,offline_readers,offline_supply_docs,offline_finance_docs,offline_build,sim_human,e2e_ingest_L0}.py` (реальные документы L0, без сети).

**Осталось**: OCR/ИИ-зрение для изображений (флаг включения, записанные ответы); денежные проверки C1/C2/C3/S1 и классификация L3 — у другого потока; L1–L4 (шум в документах) — этот конвейер проверен только на чистом L0; калибровка двойника, оптимизация, оценка на истине — следующий этап.
## Этап 7

Одно приложение FastAPI: `src/lagrange_rx/api/app.py` (`app`, `create_app(data_dir, ...)`), собранное из роутеров.

| Роутер | Пути |
|---|---|
| `api/ingest.py` (`make_router`) | `/ingest/batches*`, `/ingest/review/{id}`, `/ingest/documents/{id}/page/{n}.png`; состояние в `LAGRANGE_DATA_DIR` |
| `api/ontology.py` | `GET /ontology/rules`, `GET /ontology/graphs`, `GET /ontology/graphs/{id}`, `POST /ontology/validate` |
| `api/engine.py` (`make_router`) | `POST /graphs`, `/engine/twin`, `/engine/scenarios`, `/engine/optimize`, `/engine/what-if`, `GET /engine/plans/{id}` |
| `app.py` | `GET /health`; статический фронтенд из `web/` (или `LAGRANGE_WEB_DIR`) монтируется в `/`, если есть `index.html` |

- Граф, закоммиченный в `/ingest/batches/{id}/commit`, лежит в `graphs/{batch_id}.json`; `graph_id = batch_id` разрешается в `/engine/*` и `/ontology/graphs/{id}`.
- Единственный шов между онтологией и ядром: `api/seam.py::graph_for_engine`. Пока ядро работает на `engine._legacy_ontology.ClientGraph`, граф из ingest отдаёт 422 `graph_not_usable_by_engine`; после миграции ядра меняется только этот файл.
- `api.ingest.create_app` и `api.engine.create_app` сохранены (старые тесты).
- CLI: `lagrange-rx serve` (или `python -m lagrange_rx serve`) слушает только `127.0.0.1`; не-loopback хост отвергается без `--allow-non-loopback`. Аутентификации нет.
- `make install|test|run`, `Dockerfile`, `.env.example` (без секретов).
- Сквозной тест: `tests/e2e/test_http_flow.py` (два xfail с точными причинами: ядро ещё на legacy-графе; коммит маленьких фикстур даёт пустой граф).

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
