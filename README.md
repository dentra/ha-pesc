[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License][license-shield]][license]
[![Support author][donate-tinkoff-shield]][donate-tinkoff]
[![Support author][donate-boosty-shield]][donate-boosty]

[license-shield]: https://img.shields.io/static/v1?label=Лицензия&message=MIT&color=orange&logo=license
[license]: https://opensource.org/licenses/MIT
[donate-tinkoff-shield]: https://img.shields.io/static/v1?label=Поддержать+автора&message=Т-Банк&color=yellow
[donate-tinkoff]: https://www.tinkoff.ru/cf/3dZPaLYDBAI
[donate-boosty-shield]: https://img.shields.io/static/v1?label=Поддержать+автора&message=Boosty&color=red
[donate-boosty]: https://boosty.to/dentra

# Интеграция Петроэлектросбыт (ПСК/ЕИРЦ) для Home Assistant

Интеграция позволяет получить доступ к информации о переданных показателей счетчиков [Петроэлектросбыт (ПСК/ЕИРЦ)](https://ikus.pesc.ru/), а так же предоставляет сервис обновления показаний.

## Установка

- Откройте HACS->Интеграции->(меню "три точки")->Пользовательские репозитории
- Добавьте пользовательский репозиторий `dentra/ha-pesc` в поле репозиторий, в поле Категория выберете `Интеграция`

## Настройка

- Откройте Конфигурация->Устройсва и службы->Добавить интеграцию
- В поисковой строке введите `pesc` и выберети интеграцию `Pesc`
- Введите немер телефона и пароль

## Использование

В зависимости от данных лицевого счта, будут созданы соотвествующие службы и сенсоры.

По-умолчанию, обновление данных происходит раз в 12 часов, Вы всегда можете изменить этот парамтр в настройках службы.

## Изменение значений

Используйте визуальный редактор и действие `pesc.update_value`: в поле «Показания по сенсорам» добавьте сенсоры и их значения.
Показания группируются по счётчикам, по одному запросу на счётчик.

```yaml
alias: Отправка показаний
sequence:
  - action: pesc.update_value
    data:
      values:
        - entity_id: sensor.pesc_0123456789_2
          value: 12345
        - entity_id: sensor.pesc_0123456789_3
          value: 6789
        - entity_id: sensor.pesc_9876543210_1
          value: 202
    response_variable: response
  - condition: template
    value_template: "{{ response.code == 0 }}"
  - action: notify.notify_me
    data:
      message: Показания успешно переданы
```

Если один из счётчиков не принял показания, остальные всё равно передаются.
В ответе `code` равен 0, если приняты все показания, иначе это код первой ошибки, а в `results` результат по каждому счётчику.
С `throws: false` ошибка не прерывает скрипт, а только пишется в журнал.

Прежний формат по-прежнему поддерживается в YAML:

```yaml
action: pesc.update_value
target:
  entity_id: sensor.pesc_0123456789_1
data:
  value: 12345
```

```yaml
action: pesc.update_value
target:
  entity_id:
    - sensor.pesc_0123456789_2
    - sensor.pesc_0123456789_3
data:
  value:
    - scale_id: 2
      value: 12345
    - scale_id: 3
      value: 6789
```

## Показания из других интеграций

Если показания счётчика уже есть в Home Assistant (например, от умного счётчика), их можно передавать без ручного ввода.
В настройках интеграции на шаге «Источники показаний» свяжите сенсоры показаний с сенсорами-источниками.
Единицы измерения пересчитываются автоматически (Вт·ч → кВт·ч, л → м³), дробная часть отбрасывается.

Передать показания можно действием `pesc.send_linked`, например в автоматизации раз в месяц:

```yaml
action: pesc.send_linked
response_variable: response
```

Или кнопкой «Передать показания» на устройстве лицевого счёта: она появляется, если у счёта есть связи. Ненужную кнопку можно отключить в её настройках.

## Получение стоимости тарифа

Начиная с версии от 22.05.2023 сенсоры со стоимостью тарифа можно добавить автоматически,
включив соответсвующую опцию в настройках службы.

## Логирование

Логирование можно включить, добавив следующие строки в configuration.yaml или пакет:

```yaml
logger:
  logs:
    custom_components.pesc: debug
```

## Ваша благодарность

Если этот проект оказался для вас полезен и/или вы хотите поддержать его дальнейше развитие, то всегда можно оставить вашу благодарность [переводом на карту](https://www.tinkoff.ru/cf/3dZPaLYDBAI), [разовыми донатом или подпиской на boosty](https://boosty.to/dentra) или просто поставив звезду.
