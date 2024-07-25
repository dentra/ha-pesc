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

Используйте визульный редактор и службу `pesc.update_value`

Или воспользуйтесь примером ниже:

```yaml
service: pesc.update_value
target:
  entity_id: sensor.pesc_98765432_1
data:
  value: 12345
```

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
