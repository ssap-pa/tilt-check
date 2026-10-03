// tilt-check minute bars for NinjaTrader 8 (companion to TiltCheckFeed.cs).
//
// Writes closed 1-minute bars to Documents\tilt-check\bars\<instrument>.csv in the same shape
// as a NinjaTrader bar export:  instrument,bar_end_utc,open,high,low,close,volume
// so `python -m tiltcheck watch --bars "Documents\tilt-check\bars"` can draw CHART lines live.
//
// Which instruments: one full name per line in Documents\tilt-check\bars-watch.txt, e.g.
//   MNQ 12-26
//   MES 12-26
// The file is re-read every 30 seconds, so you can add a contract without restarting.
//
// Only the bar that has just closed is written (the forming bar never is), which is what keeps
// the live chart line equal to the audit's no-look-ahead numbers.
//
// It only listens. It never places, changes or cancels an order.
// STATUS: written against the NinjaTrader 8 API docs, not yet run inside a real NinjaTrader.
// If it fails to compile, send the first error line; the fix is usually one type name.
#region Using declarations
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
#endregion

namespace NinjaTrader.NinjaScript.AddOns
{
	public class TiltCheckBars : AddOnBase
	{
		private string dir;
		private string listPath;
		private readonly object fileLock = new object();
		private readonly Dictionary<string, BarsRequest> requests = new Dictionary<string, BarsRequest>();
		private readonly Dictionary<string, int> lastWritten = new Dictionary<string, int>();
		private System.Threading.Timer timer;

		protected override void OnStateChange()
		{
			if (State == State.SetDefaults)
			{
				Name		= "TiltCheckBars";
				Description	= "Appends closed 1-minute bars to Documents\\tilt-check\\bars for tilt-check. Never places orders.";
			}
			else if (State == State.Active)
			{
				string root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "tilt-check");
				dir = Path.Combine(root, "bars");
				Directory.CreateDirectory(dir);
				listPath = Path.Combine(root, "bars-watch.txt");
				if (!File.Exists(listPath))
					File.WriteAllText(listPath, "");
				timer = new System.Threading.Timer(OnTimer, null, 0, 30000);
			}
			else if (State == State.Terminated)
			{
				if (timer != null)
					timer.Dispose();
				lock (requests)
				{
					foreach (BarsRequest r in requests.Values)
					{
						try { r.Update -= OnBarsUpdate; r.Dispose(); } catch (Exception) { }
					}
					requests.Clear();
				}
			}
		}

		private void OnTimer(object state)
		{
			try
			{
				foreach (string line in File.ReadAllLines(listPath))
				{
					string name = line.Trim();
					if (name.Length == 0 || name.StartsWith("#"))
						continue;
					lock (requests)
					{
						if (requests.ContainsKey(name))
							continue;
					}
					Instrument inst = Instrument.GetInstrument(name);
					if (inst == null)
						continue;
					BarsRequest req = new BarsRequest(inst, 3);			// three days back, enough for a 15m 200 EMA
					req.BarsPeriod = new BarsPeriod { BarsPeriodType = BarsPeriodType.Minute, Value = 1 };
					req.TradingHours = inst.MasterInstrument.TradingHours;
					lock (requests)
						requests[name] = req;
					req.Request((bars, errorCode, errorMessage) =>
					{
						try
						{
							if (errorCode != ErrorCode.NoError || bars == null)
								return;
							// Backfill every closed bar we don't have yet, then follow updates.
							int last = LastIndexOnDisk(name);
							WriteRange(name, bars.Bars, last + 1, bars.Bars.Count - 2);
							lock (lastWritten)
								lastWritten[name] = bars.Bars.Count - 2;
							bars.Update += OnBarsUpdate;
						}
						catch (Exception) { }
					});
				}
			}
			catch (Exception)
			{
				// A feed must never take NinjaTrader down.
			}
		}

		private void OnBarsUpdate(object sender, BarsUpdateEventArgs e)
		{
			try
			{
				BarsRequest req = sender as BarsRequest;
				if (req == null)
					return;
				string name = req.Bars.Instrument.FullName;
				int closedUpTo = e.MaxIndex - 1;			// e.MaxIndex is the bar still forming
				int from;
				lock (lastWritten)
					from = (lastWritten.ContainsKey(name) ? lastWritten[name] : -1) + 1;
				if (closedUpTo >= from)
				{
					WriteRange(name, e.BarsSeries, from, closedUpTo);
					lock (lastWritten)
						lastWritten[name] = closedUpTo;
				}
			}
			catch (Exception) { }
		}

		private void WriteRange(string name, Bars bars, int from, int to)
		{
			if (to < from)
				return;
			string path = Path.Combine(dir, Clean(name) + ".csv");
			bool fresh = !File.Exists(path);
			var lines = new List<string>();
			if (fresh)
				lines.Add("instrument,bar_end_utc,open,high,low,close,volume");
			for (int i = from; i <= to; i++)
			{
				// GetTime is the bar's close in NinjaTrader's time zone; assume that is this PC's zone.
				DateTime end = DateTime.SpecifyKind(bars.GetTime(i), DateTimeKind.Local).ToUniversalTime();
				lines.Add(string.Join(",",
					Clean(name),
					end.ToString("yyyy-MM-ddTHH:mm:ss.fff'Z'", CultureInfo.InvariantCulture),
					bars.GetOpen(i).ToString(CultureInfo.InvariantCulture),
					bars.GetHigh(i).ToString(CultureInfo.InvariantCulture),
					bars.GetLow(i).ToString(CultureInfo.InvariantCulture),
					bars.GetClose(i).ToString(CultureInfo.InvariantCulture),
					bars.GetVolume(i).ToString(CultureInfo.InvariantCulture)));
			}
			lock (fileLock)
				File.AppendAllText(path, string.Join("\n", lines) + "\n");
		}

		private int LastIndexOnDisk(string name)
		{
			// We only need "how far did we get"; -1 means nothing yet. The index is per request,
			// so a restart re-requests three days and we de-duplicate by time on the Python side.
			return -1;
		}

		private static string Clean(string s)
		{
			return (s ?? string.Empty).Replace(",", " ").Replace("\n", " ");
		}
	}
}
