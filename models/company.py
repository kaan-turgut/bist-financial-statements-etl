from dataclasses import dataclass


@dataclass
class Company:
    symbol: str
    oid: str
    name: str
    website: str | None
    city: str | None
    sectors: list
    indexes: list
    activity: str | None
    paid_in_capital: float | None
    capital_ceiling: float | None
    shares_outstanding: float | None
    capital_details: dict | list | str | None

    @classmethod
    def from_kap_data(cls, symbol, data):
        return cls(
            symbol=symbol,
            oid=data.get("mkkMemberOid"),
            name=data.get("kapMemberTitle"),
            website=data.get("Website"),
            city=data.get("cityName"),
            sectors=data.get("Sectors") or [],
            indexes=data.get("Indexes") or [],
            activity=data.get("activityField"),
            paid_in_capital=data.get("paidInCapital"),
            capital_ceiling=data.get("capitalCeiling"),
            shares_outstanding=data.get("numSharesOutstanding"),
            capital_details=data.get("capitalDetails"),
        )
