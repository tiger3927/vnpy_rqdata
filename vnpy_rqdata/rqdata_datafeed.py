from datetime import timedelta
from typing import List, Optional
from pytz import timezone
from datetime import datetime

from numpy import ndarray
from rqdatac import init
from rqdatac.services.get_price import get_price
from rqdatac.services.basic import all_instruments
from rqdatac.share.errors import AuthenticationFailed

from vnpy.trader.setting import SETTINGS
from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData, TickData, HistoryRequest
from vnpy.trader.utility import round_to
from vnpy.trader.datafeed import BaseDatafeed


INTERVAL_VT2RQ = {
    Interval.MINUTE: "1m",
    Interval.HOUR: "60m",
    Interval.DAILY: "1d",
}

INTERVAL_ADJUSTMENT_MAP = {
    Interval.MINUTE: timedelta(minutes=1),
    Interval.HOUR: timedelta(hours=1),
    Interval.DAILY: timedelta()         # no need to adjust for daily bar
}

CHINA_TZ = timezone("Asia/Shanghai")


def to_rq_symbol(symbol: str, exchange: Exchange) -> str:
    """将交易所代码转换为米筐代码"""
    # 股票
    if exchange in [Exchange.SSE, Exchange.SZSE]:
        if exchange == Exchange.SSE:
            rq_symbol = f"{symbol}.XSHG"
        else:
            rq_symbol = f"{symbol}.XSHE"
    # 金交所现货
    elif exchange in [Exchange.SGE]:
        for char in ["(", ")", "+"]:
            symbol = symbol.replace(char, "")
        symbol = symbol.upper()
        rq_symbol = f"{symbol}.SGEX"
    # 期货和期权
    elif exchange in [Exchange.SHFE, Exchange.CFFEX, Exchange.DCE, Exchange.CZCE, Exchange.INE]:
        for count, word in enumerate(symbol):
            if word.isdigit():
                break

        product = symbol[:count]
        time_str = symbol[count:]

        # 期货
        if time_str.isdigit():
            if exchange is not Exchange.CZCE:
                return symbol.upper()

            # 检查是否为连续合约或者指数合约
            if time_str in ["88", "888", "99", "889"]:
                return symbol

            year = symbol[count]
            month = symbol[count + 1:]

            if year == "9":
                year = "1" + year
            else:
                year = "2" + year

            rq_symbol = f"{product}{year}{month}".upper()
        # 期权
        else:
            if exchange in [Exchange.CFFEX, Exchange.DCE, Exchange.SHFE]:
                rq_symbol = symbol.replace("-", "").upper()
            elif exchange == Exchange.CZCE:
                year = symbol[count]
                suffix = symbol[count + 1:]

                if year == "9":
                    year = "1" + year
                else:
                    year = "2" + year

                rq_symbol = f"{product}{year}{suffix}".upper()
    else:
        rq_symbol = f"{symbol}.{exchange.value}"

    return rq_symbol


class RqdataDatafeed(BaseDatafeed):
    """米筐RQData数据服务接口"""

    def __init__(self):
        """"""
        self.username: str = SETTINGS["datafeed.username"]
        self.password: str = SETTINGS["datafeed.password"]

        self.inited: bool = False
        self.symbols: ndarray = None

    def init(self) -> bool:
        """初始化"""
        if self.inited:
            return True

        if not self.username or not self.password:
            return False

        try:
            init(
                self.username,
                self.password,
                ("rqdatad-pro.ricequant.com", 16011),
                use_pool=True,
                max_pool_size=1,
                auto_load_plugins=False
            )

            df = all_instruments()
            self.symbols = df["order_book_id"].values
        except (RuntimeError, AuthenticationFailed):
            return False

        self.inited = True
        return True

    def query_bar_history(self, req: HistoryRequest) -> Optional[List[BarData]]:
        """查询K线数据"""
        if not self.inited:
            n = self.init()
            if not n:
                return []

        symbol = req.symbol
        exchange = req.exchange
        interval = req.interval
        start = req.start
        end = req.end

        rq_symbol = to_rq_symbol(symbol, exchange)

        rq_interval = INTERVAL_VT2RQ.get(interval)
        if not rq_interval:
            return None

        # 为了将米筐时间戳（K线结束时点）转换为VeighNa时间戳（K线开始时点）
        adjustment = INTERVAL_ADJUSTMENT_MAP[interval]

        # 为了查询夜盘数据
        end += timedelta(1)

        # 只对衍生品合约才查询持仓量数据
        fields = ["open", "high", "low", "close", "volume", "total_turnover"]
        if not symbol.isdigit():
            fields.append("open_interest")

        df = get_price(
            rq_symbol,
            frequency=rq_interval,
            fields=fields,
            start_date=start,
            end_date=end,
            adjust_type="none"
        )

        data: List[BarData] = []

        if df is not None:
            for ix, row in df.iterrows():
                dt = row.name[1].to_pydatetime() - adjustment
                dt = CHINA_TZ.localize(dt)

                bar = BarData(
                    symbol=symbol,
                    exchange=exchange,
                    interval=interval,
                    datetime=dt,
                    open_price=round_to(row["open"], 0.000001),
                    high_price=round_to(row["high"], 0.000001),
                    low_price=round_to(row["low"], 0.000001),
                    close_price=round_to(row["close"], 0.000001),
                    volume=row["volume"],
                    turnover=row["total_turnover"],
                    open_interest=row.get("open_interest", 0),
                    gateway_name="RQ"
                )

                data.append(bar)

        return data

    def query_tick_history(self, req: HistoryRequest) -> Optional[List[TickData]]:
        """查询Tick数据"""
        if not self.inited:
            n = self.init()
            if not n:
                return []

        symbol = req.symbol
        exchange = req.exchange
        start = req.start
        end = req.end

        rq_symbol = to_rq_symbol(symbol, exchange)
        if rq_symbol not in self.symbols:
            return None

        # 为了查询夜盘数据
        end += timedelta(1)

        # 只对衍生品合约才查询持仓量数据
        fields = [
            "open",
            "high",
            "low",
            "last",
            "prev_close",
            "volume",
            "total_turnover",
            "limit_up",
            "limit_down",
            "b1",
            "b2",
            "b3",
            "b4",
            "b5",
            "a1",
            "a2",
            "a3",
            "a4",
            "a5",
            "b1_v",
            "b2_v",
            "b3_v",
            "b4_v",
            "b5_v",
            "a1_v",
            "a2_v",
            "a3_v",
            "a4_v",
            "a5_v",
        ]
        if not symbol.isdigit():
            fields.append("open_interest")

        df = get_price(
            rq_symbol,
            frequency="tick",
            fields=fields,
            start_date=start,
            end_date=end,
            adjust_type="none"
        )

        data: List[TickData] = []

        if df is not None:
            for ix, row in df.iterrows():
                dt = row.name[1].to_pydatetime()
                dt = CHINA_TZ.localize(dt)

                tick = TickData(
                    symbol=symbol,
                    exchange=exchange,
                    datetime=dt,
                    open_price=row["open"],
                    high_price=row["high"],
                    low_price=row["low"],
                    pre_close=row["prev_close"],
                    last_price=row["last"],
                    volume=row["volume"],
                    turnover=row["total_turnover"],
                    open_interest=row.get("open_interest", 0),
                    limit_up=row["limit_up"],
                    limit_down=row["limit_down"],
                    bid_price_1=row["b1"],
                    bid_price_2=row["b2"],
                    bid_price_3=row["b3"],
                    bid_price_4=row["b4"],
                    bid_price_5=row["b5"],
                    ask_price_1=row["a1"],
                    ask_price_2=row["a2"],
                    ask_price_3=row["a3"],
                    ask_price_4=row["a4"],
                    ask_price_5=row["a5"],
                    bid_volume_1=row["b1_v"],
                    bid_volume_2=row["b2_v"],
                    bid_volume_3=row["b3_v"],
                    bid_volume_4=row["b4_v"],
                    bid_volume_5=row["b5_v"],
                    ask_volume_1=row["a1_v"],
                    ask_volume_2=row["a2_v"],
                    ask_volume_3=row["a3_v"],
                    ask_volume_4=row["a4_v"],
                    ask_volume_5=row["a5_v"],
                    gateway_name="RQ"
                )

                data.append(tick)

        return data

if __name__ == "__main__":

    datafeed = RqdataDatafeed()
    r = HistoryRequest(symbol="rb2205", exchange=Exchange.SHFE, interval=Interval.DAILY,
                       start=datetime(2022, 4, 10, 1, 0, 0), end=datetime(2022, 4,15, 1, 0, 0))
    r = datafeed.query_bar_history(r)
    print("result : ", len(r))
