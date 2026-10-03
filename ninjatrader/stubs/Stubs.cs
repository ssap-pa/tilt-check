// Minimal stand-ins for the NinjaTrader 8 types the add-ons touch. Only for a syntax/type
// compile with csc.exe outside NinjaTrader; signatures follow the NT8 docs.
using System;
using System.Collections.Generic;

namespace NinjaTrader.Cbi
{
	public enum State { SetDefaults, Configure, Active, Terminated }
	public enum MarketPosition { Flat, Long, Short }
	public enum Currency { UsDollar }
	public enum AccountItem { UnrealizedProfitLoss, CashValue }
	public enum OrderAction { Buy, Sell }
	public enum OrderType { Market, Limit, StopMarket }
	public enum OrderState { Working, Filled, Cancelled }
	public enum ErrorCode { NoError, Panic }

	public class TradingHours { }
	public class MasterInstrument { public TradingHours TradingHours; }
	public class Instrument
	{
		public string FullName;
		public MasterInstrument MasterInstrument = new MasterInstrument();
		public static Instrument GetInstrument(string name) { return new Instrument { FullName = name }; }
	}
	public class Position { public MarketPosition MarketPosition; }
	public class Execution { public Account Account; public Instrument Instrument; }
	public class Order
	{
		public Account Account; public Instrument Instrument; public string OrderId;
		public OrderAction OrderAction; public OrderType OrderType;
	}
	public class ExecutionEventArgs : EventArgs
	{
		public Execution Execution; public MarketPosition MarketPosition; public int Quantity;
		public double Price; public string ExecutionId; public DateTime Time;
	}
	public class OrderEventArgs : EventArgs
	{
		public Order Order; public OrderState OrderState; public int Quantity; public double StopPrice; public double LimitPrice;
	}
	public class Account
	{
		public string Name;
		public List<Position> Positions = new List<Position>();
		public static List<Account> All = new List<Account>();
		public event EventHandler<ExecutionEventArgs> ExecutionUpdate;
		public event EventHandler<OrderEventArgs> OrderUpdate;
		public double Get(AccountItem item, Currency c) { return 0; }
	}
}

namespace NinjaTrader.Data
{
	using NinjaTrader.Cbi;
	public enum BarsPeriodType { Minute, Day }
	public class BarsPeriod { public BarsPeriodType BarsPeriodType; public int Value; }
	public class Bars
	{
		public Instrument Instrument;
		public int Count;
		public DateTime GetTime(int i) { return DateTime.Now; }
		public double GetOpen(int i) { return 0; }
		public double GetHigh(int i) { return 0; }
		public double GetLow(int i) { return 0; }
		public double GetClose(int i) { return 0; }
		public long GetVolume(int i) { return 0; }
	}
	public class BarsUpdateEventArgs : EventArgs { public Bars BarsSeries; public int MinIndex; public int MaxIndex; }
	public class BarsRequest : IDisposable
	{
		public BarsPeriod BarsPeriod; public TradingHours TradingHours; public Bars Bars;
		public event EventHandler<BarsUpdateEventArgs> Update;
		public BarsRequest(Instrument i, int lookbackDays) { }
		public void Request(Action<BarsRequest, ErrorCode, string> callback) { }
		public void Dispose() { }
	}
}

namespace NinjaTrader.NinjaScript
{
	using NinjaTrader.Cbi;
	public abstract class AddOnBase
	{
		public string Name; public string Description;
		public State State = State.SetDefaults;
		protected virtual void OnStateChange() { }
	}
}
