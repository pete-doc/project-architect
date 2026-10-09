# MODULES — карта модулей

> **Как пользоваться.** Здесь перечислены все модули проекта: одна строка на модуль. CI проверяет, что каждый
> модуль в коде есть в этой таблице. Агент-уборщик (janitor) удаляет подтверждённый мёртвый код, но не трогает
> модули со статусом `keep-until`.
>
> **Статус:** `active` (используется), `keep-until:ГГГГ-ММ-ДД` (держим до этой даты, не удалять),
> `deprecated` (будет удалён).
>
> **Блок:** номер блока из `state/features.json` (`F7`, можно несколько через запятую), ради которого существует
> модуль. Блок указывает на цель, поэтому по колонке видно, зачем нужен код. Модуль без блока и без `keep-until`
> CI считает кодом без цели: проверка `modules` падает.
>
> **Корни кода продукта** заданы в `pyproject.toml` (`[tool.parch] source_roots`): `plugin/hooks`,
> `plugin/templates/ci/parch`, `plugin/skills`, `scripts`, `.circleci` (F25, самоприменение).

| Модуль | Путь | Назначение (одной фразой) | Язык | Статус | Блок |
|---|---|---|---|---|---|
| _common | plugin/hooks/_common.py | Общие функции hooks: чтение входа, ответ блокировки, поиск CONSTITUTION | Python | active | F2 |
| audit_log | plugin/hooks/audit_log.py | Журнал всех вызовов инструментов в `.claude/audit/` | Python | active | F2 |
| guard_destructive | plugin/hooks/guard_destructive.py | Блокирует необратимые команды и обращения к секретам | Python | active | F2 |
| guard_packages | plugin/hooks/guard_packages.py | Установка пакетов только из списка разрешённых в CONSTITUTION | Python | active | F2 |
| guard_paths | plugin/hooks/guard_paths.py | Охрана путей: тесты, правила проверок и CONSTITUTION закрыты для основной сессии | Python | active | F2 |
| guard_shell_writes | plugin/hooks/guard_shell_writes.py | Запись файлов проекта через командную строку запрещена, только Write и Edit | Python | active | F2 |
| loop_guard | plugin/hooks/loop_guard.py | После петли блокирует правки кода до конца сессии | Python | active | F13 |
| post_edit_check | plugin/hooks/post_edit_check.py | После правки файла запускает форматтер, линтер и проверку типов его языка | Python | active | F2 |
| pre_push | plugin/hooks/pre_push.py | Перед `git push` запускает проверку `standard` | Python | active | F2 |
| stop_gate | plugin/hooks/stop_gate.py | Не даёт агенту остановиться, пока не проходят проверки проекта | Python | active | F2 |
| parch_catalog | plugin/templates/ci/parch/parch_catalog.py | Каталог возможностей и обязательные описания публичных функций | Python | active | F15 |
| parch_ci | plugin/templates/ci/parch/parch_ci.py | Проверки CI общие для всех языков и храповик baseline | Python | active | F4 |
| parch_libraries | plugin/templates/ci/parch/parch_libraries.py | Правило «низкоуровневая библиотека в одном модуле» для C#, TypeScript и PowerShell | Python | active | F15 |
| parch_standard | plugin/templates/ci/parch/parch_standard.py | Правила состава проекта для проверки `standard` | Python | active | F14 |
| parch_state | plugin/templates/ci/parch/parch_state.py | Табло в CircleCI: итог тестов, пометка, публикация на ветку `status` | Python | active | F24 |
| parch_status | plugin/templates/ci/parch/parch_status.py | Генератор табло `state/STATUS.md` | Python | active | F12 |
| adr | plugin/skills/adr | Навык `/parch:adr`: запись решения из шаблона и индекс `docs/adr/README.md` | Python | active | F3 |
| analyze-existing | plugin/skills/analyze-existing | Навык `/parch:analyze-existing`: карта модулей, мёртвый код, история, план приведения к стандарту | Python | active | F15 |
| doctor | plugin/skills/doctor | Навык `/parch:doctor`: проверка, что защита работает на этом компьютере | Python | active | F3 |
| init-project | plugin/skills/init-project | Навык `/parch:init-project`: подключает проект к ProjectArchitect | Python | active | F3 |
| trace | plugin/skills/trace | Навык `/parch:trace`: от кода до цели | Python | active | F18 |
| preflight | scripts/preflight.py | Единая команда «проверить перед отправкой» | Python | active | F1 |
| release_check | scripts/release_check.py | Сверка версии плагина с последним тегом выпуска | Python | active | F1 |
| state_inputs | .circleci/state_inputs.py | Данные для табло: открытые PR репозитория | Python | active | F24 |
