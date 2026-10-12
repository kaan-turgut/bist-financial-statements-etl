from dataclasses import dataclass


@dataclass
class Company:
    symbol: str
    oid: str
    name: str
    website: str
    city: str
    sectors: list
    indexes: list
    activity: str
    paid_in_capital: float
    capital_ceiling: float
    shares_outstanding: float
    capital_details: dict | list | str | None

    @classmethod
    def from_kap_data(cls, symbol, data):
        return cls(
            symbol=symbol,
            oid=data["mkkMemberOid"],
            name=data["kapMemberTitle"],
            website=data["Website"],
            city=data["cityName"],
            sectors=data["Sectors"],
            indexes=data["Indexes"],
            activity=data["activityField"],
            paid_in_capital=data["paidInCapital"],
            capital_ceiling=data["capitalCeiling"],
            shares_outstanding=data["numSharesOutstanding"],
            capital_details=data["capitalDetails"],
        )
