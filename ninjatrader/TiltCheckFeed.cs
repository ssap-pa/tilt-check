// tilt-check live feed for NinjaTrader 8.
//
// Writes to Documents\tilt-check, for `python -m tiltcheck watch`:
//   fills.csv      every fill on every connected account
//   orders.csv     every order update (so watch can see whether a stop is working and where)
//   snapshots.csv  unrealized P&L and cash value: every 2 s with a position open, every 60 s flat
//
// It only listens. It never places, changes or cancels an order.
//
// Install: copy this file to Documents\NinjaTrader 8\bin\Custom\AddOns\, open
// New > NinjaScript Editor, press F5 to compile, then restart NinjaTrader.
#region Using declarations
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using NinjaTrader.Cbi;
#endregion

namespace NinjaTrader.NinjaScript.AddOns
{
	public class TiltCheckFeed : AddOnBase
	{
		private string fillsPath;
		private string ordersPath;
		private string snapshotsPath;
		private readonly object fileLock = new object();
		private readonly HashSet<Account> subscribed = new HashSet<Account>();
		private readonly Dictionary<string, DateTime> lastFlatSnapshot = new Dictionary<string, DateTime>();
		private System.Threading.Timer timer;

		protected override void OnStateChange()
		{
			if (State == State.SetDefaults)
			{
				Name		= "TiltCheckFeed";
				Description	= "Writes fills, orders and account P&L to Documents\\tilt-check for tilt-check. Never places orders.";
			}
			else if (State == State.Active)
			{
				string dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "tilt-check");
				Directory.CreateDirectory(dir);
				fillsPath		= Path.Combine(dir, "fills.csv");
				ordersPath		= Path.Combine(dir, "orders.csv");
				snapshotsPath	= Path.Combine(dir, "snapshots.csv");
				if (!File.Exists(fillsPath))
					File.WriteAllText(fillsPath, "time_utc,account,instrument,signed_qty,price,execution_id,platform_time\n");
				if (!File.Exists(ordersPath))
					File.WriteAllText(ordersPath, "time_utc,account,instrument,order_id,action,type,state,qty,stop_price,limit_price\n");
				if (!File.Exists(snapshotsPath))
					File.WriteAllText(snapshotsPath, "time_utc,account,unrealized_usd,open_positions,cash_value\n");
				// Accounts can connect after NinjaTrader starts, so look for new ones every 2 seconds.
				timer = new System.Threading.Timer(OnTimer, null, 0, 2000);
			}
			else if (State == State.Terminated)
			{
				if (timer != null)
					timer.Dispose();
				lock (subscribed)
				{
					foreach (Account a in subscribed)
					{
						a.ExecutionUpdate -= OnExecutionUpdate;
						a.OrderUpdate -= OnOrderUpdate;
					}
					subscribed.Clear();
				}
			}
		}

		private void OnTimer(object state)
		{
			try
			{
				List<Account> accounts;
				lock (Account.All)
					accounts = Account.All.ToList();
				foreach (Account a in accounts)
				{
					lock (subscribed)
					{
						if (!subscribed.Contains(a))
						{
							a.ExecutionUpdate += OnExecutionUpdate;
							a.OrderUpdate += OnOrderUpdate;
							subscribed.Add(a);
						}
					}
					int open;
					lock (a.Positions)
						open = a.Positions.Count(p => p.MarketPosition != MarketPosition.Flat);
					DateTime now = DateTime.UtcNow;
					lock (lastFlatSnapshot)
					{
						DateTime last;
						if (open == 0 && lastFlatSnapshot.TryGetValue(a.Name, out last) && (now - last).TotalSeconds < 60)
							continue;
						if (open == 0)
							lastFlatSnapshot[a.Name] = now;
					}
					double upl = a.Get(AccountItem.UnrealizedProfitLoss, Currency.UsDollar);
					double cash = a.Get(AccountItem.CashValue, Currency.UsDollar);
					Append(snapshotsPath, string.Join(",",
						now.ToString("yyyy-MM-dd HH:mm:ss.fff", CultureInfo.InvariantCulture),
						Clean(a.Name),
						upl.ToString(CultureInfo.InvariantCulture),
						open.ToString(CultureInfo.InvariantCulture),
						cash.ToString(CultureInfo.InvariantCulture)));
				}
			}
			catch (Exception)
			{
				// A feed must never take NinjaTrader down. Skip this tick.
			}
		}

		private void OnExecutionUpdate(object sender, ExecutionEventArgs e)
		{
			try
			{
				Execution x = e.Execution;
				if (x == null)
					return;
				int signedQty = e.MarketPosition == MarketPosition.Short ? -e.Quantity : e.Quantity;
				// time_utc is when the fill reached this PC; platform_time is NinjaTrader's own
				// stamp, in whatever time zone NinjaTrader is set to.
				Append(fillsPath, string.Join(",",
					DateTime.UtcNow.ToString("yyyy-MM-dd HH:mm:ss.fff", CultureInfo.InvariantCulture),
					Clean(x.Account.Name),
					Clean(x.Instrument.FullName),
					signedQty.ToString(CultureInfo.InvariantCulture),
					e.Price.ToString(CultureInfo.InvariantCulture),
					Clean(e.ExecutionId),
					e.Time.ToString("yyyy-MM-dd HH:mm:ss.fff", CultureInfo.InvariantCulture)));
			}
			catch (Exception)
			{
			}
		}

		private void OnOrderUpdate(object sender, OrderEventArgs e)
		{
			try
			{
				Order o = e.Order;
				if (o == null)
					return;
				Append(ordersPath, string.Join(",",
					DateTime.UtcNow.ToString("yyyy-MM-dd HH:mm:ss.fff", CultureInfo.InvariantCulture),
					Clean(o.Account.Name),
					Clean(o.Instrument.FullName),
					Clean(o.OrderId),
					o.OrderAction.ToString(),
					o.OrderType.ToString(),
					e.OrderState.ToString(),
					e.Quantity.ToString(CultureInfo.InvariantCulture),
					e.StopPrice.ToString(CultureInfo.InvariantCulture),
					e.LimitPrice.ToString(CultureInfo.InvariantCulture)));
			}
			catch (Exception)
			{
			}
		}

		private void Append(string path, string line)
		{
			lock (fileLock)
				File.AppendAllText(path, line + "\n");
		}

		private static string Clean(string s)
		{
			return (s ?? string.Empty).Replace(",", " ").Replace("\n", " ");
		}
	}
}
