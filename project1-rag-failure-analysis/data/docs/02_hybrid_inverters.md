# Hybrid Inverter Operation and Limits

A hybrid inverter combines a solar charge controller (MPPT), battery inverter/charger, and grid/generator transfer switch in one unit. A 5.5 kVA hybrid inverter at 0.9 power factor delivers about 5,000 W continuous AC output.

Key ratings to check: continuous output (VA and W), surge rating (typically 2x continuous for 5 seconds, needed for motor and compressor starts), maximum PV input voltage (Voc, commonly 450-500 V), MPPT voltage range (e.g., 120-430 V), and maximum PV input current per tracker (commonly 18-27 A).

PV overpaneling: most 5.5 kVA hybrid inverters accept up to 6,000-6,500 W of PV, but the MPPT will clip charging at its DC output ceiling, typically about 100 A of battery charge current at 48 V nominal (about 5,100 W into the battery bus). Clipping is not harmful; it simply caps midday harvest.

Transfer behavior: in SBU (Solar-Battery-Utility) priority mode, the inverter uses solar first, battery second, and grid only when battery voltage falls below the configured cut-in point. Transfer time from grid to battery is 10-20 ms for high-frequency units, fast enough for computers but occasionally visible as a flicker on sensitive AV equipment.

Common failure symptom: an F07 or overload fault during compressor start indicates surge exceeding the inverter's 5-second rating. Fix options are soft-start kits on the compressor, staggering loads, or moving to a low-frequency inverter with a 3x surge rating.
