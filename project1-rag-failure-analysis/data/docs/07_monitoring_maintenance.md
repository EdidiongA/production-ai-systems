# Monitoring, Data Logging, and Maintenance

Modern hybrid inverters expose telemetry over RS485/Modbus or a Wi-Fi dongle: PV power, battery voltage, current and SoC, load power, and grid import. Log at 1-minute resolution; 5-minute averages hide the surge events that explain overload faults.

Key health indicators to trend weekly: (1) daily PV yield in kWh versus a clear-day baseline - a persistent 10 percent drop indicates soiling or a failed string; (2) battery charge acceptance - time from 20 to 100 percent SoC on a clear day should be stable; (3) depth of nightly discharge - creeping DoD indicates load growth or battery capacity fade.

Panel cleaning: in dusty dry-season conditions (harmattan, December-February in West Africa), soiling losses reach 15-25 percent within 3-4 weeks. Clean panels with water and a soft brush at dawn or dusk; cleaning hot glass with cold water risks thermal shock cracking.

Battery capacity testing: once per year, run a controlled discharge from 100 percent to the low-voltage cutoff at a steady 0.2C load and integrate amp-hours with a shunt monitor. Compare against nameplate; below 80 percent remaining capacity, plan replacement.

Firmware: hybrid inverter firmware updates frequently change battery communication protocols (CAN/RS485 BMS pairing). Record the working firmware version and only update with the battery vendor's compatibility note in hand.
