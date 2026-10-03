# Compile check without NinjaTrader

`Stubs.cs` holds stand-ins for the NinjaTrader 8 types the add-ons use, so you can catch
syntax and type errors on any Windows PC with the .NET Framework compiler:

```
"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:library /out:check.dll ninjatrader\stubs\Stubs.cs ninjatrader\TiltCheckFeed.cs ninjatrader\TiltCheckBars.cs
```

No errors means the C# is well-formed against these signatures. It does not prove the add-ons
run inside NinjaTrader; that still needs a real install (New > NinjaScript Editor > F5).
