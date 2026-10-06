from custom_components.pesc.pesc_api import Tariff, TariffRate


class _Ind:
    name = "Неизвестно"
    scale_id = 9


def test_tariff_rate_unmatched() -> None:
    tariff = Tariff("Отопление", None, [TariffRate(1.0, "a"), TariffRate(2.0, "b")])

    assert tariff.rate(_Ind()) is None
