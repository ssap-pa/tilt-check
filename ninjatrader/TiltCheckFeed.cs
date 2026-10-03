// tilt-check live feed for NinjaTrader 8.
//
// Writes every fill on every connected account to Documents\tilt-check\fills.csv,
// and, while a position is open, the account's unrealized P&L every 2 seconds to
// Documents\tilt-check\snapshots.csv. `python -m tiltcheck watch` reads those files.
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
		private string snapshotsPath;
		private readonly object fileLock = new object();
		private readonly HashSet<Account> subscribed = new HashSet<Account>();
		private System.Threading.Timer timer;

		protected override void OnStateChange()
		{
			if (State == State.SetDefaults)
			{
				Name		= "TiltCheckFeed";
				Description	= "Writes fills and unrealized P&L to Documents\\tilt-check for tilt-check. Never places orders.";
			}
			else if (State == State.Active)
			{
				string dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "tilt-check");
				Directory.CreateDirectory(dir);
				fillsPath		= Path.Combine(dir, "fills.csv");
				snapshotsPath	= Path.Combine(dir, "snapshots.csv");
				if (!File.Exists(fillsPath))
					File.WriteAllText(fillsPath, "time_utc,account,instrument,signed_qty,price,execution_id,platform_time\n");
				if (!File.Exists(snapshotsPath))
					File.WriteAllText(snapshotsPath, "time_utc,account,unrealized_usd,open_positions\n");
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
						a.ExecutionUpdate -= OnExecutionUpdate;
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
							subscribed.Add(a);
						}
					}
					int open;
					lock (a.Positions)
						open = a.Positions.Count(p => p.MarketPosition != MarketPosition.Flat);
					if (open > 0)
					{
						double upl = a.Get(AccountItem.UnrealizedProfitLoss, Currency.UsDollar);
						Append(snapshotsPath, string.Join(",",
							DateTime.UtcNow.ToString("yyyy-MM-dd HH:mm:ss.fff", CultureInfo.InvariantCulture),
							Clean(a.Name),
							upl.ToString(CultureInfo.InvariantCulture),
							open.ToString(CultureInfo.InvariantCulture)));
					}
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
