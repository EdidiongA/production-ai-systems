# LiFePO4 Battery Banks: Sizing, Charging, and Longevity

Lithium iron phosphate (LiFePO4) is the standard chemistry for stationary home storage due to thermal stability and cycle life. A quality LiFePO4 bank delivers 4,000-6,000 cycles to 80 percent remaining capacity at 80 percent depth of discharge (DoD), versus 400-600 cycles for lead-acid at 50 percent DoD.

Usable energy: an 18 kWh LiFePO4 bank at 90 percent usable DoD provides about 16.2 kWh of usable storage. Unlike lead-acid, LiFePO4 does not need to be held at float; float charging above 55.2 V (for a 48 V/16S bank) accelerates cell aging.

Recommended charge parameters for a 48 V (16S) LiFePO4 bank: bulk/absorb at 55.2-56.0 V, float at 54.0 V or disabled, low-voltage cutoff at 46.0-47.0 V. Charging above 0.5C is unnecessary; 0.2-0.3C (for example 70-100 A on a 350 Ah bank) balances speed and longevity.

Temperature limits: never charge below 0 C (the BMS should enforce this); sustained operation above 45 C roughly halves calendar life. In Lagos ambient conditions (26-34 C), install the bank in a ventilated, shaded space and expect 10-15 percent calendar-life reduction versus a 25 C reference.

State of charge (SoC) estimation on LiFePO4 is unreliable from voltage alone because the discharge curve is flat between 20 and 90 percent SoC. Coulomb-counting BMS or shunt-based monitors are required for accurate SoC.
