# DC Wiring, Protection, and Earthing

PV string wiring must be sized for both ampacity and voltage drop. Keep DC voltage drop under 2 percent: for a 30 m run at 20 A on a 400 V string, 4 mm2 PV1-F cable suffices; at 48 V battery-side currents of 100 A, 35-50 mm2 cable is required for even 3 m runs.

Battery-to-inverter protection: a Class T or NH00 fuse rated 1.25x maximum continuous current, placed within 30 cm of the battery positive terminal. For a 5 kW inverter at 48 V (about 110 A continuous), a 150 A Class T fuse is standard. MCBs marketed as "DC" at 125 A are frequently AC breakers relabeled; they cannot safely break a 48 V battery fault current.

PV disconnects: each string needs a DC isolator rated above string Voc at the lowest expected temperature. Voc rises about 0.3 percent per degree C below 25 C; in Lagos this correction is small, but isolators should still be rated 600 V or more for strings approaching 450 V open-circuit.

Earthing: bond panel frames and mounting rails to the building earth with 6 mm2 minimum copper. A Type 2 surge protection device (SPD) on the PV input and another on the AC output protects the inverter from induced surges; in high-lightning regions such as coastal West Africa, SPDs are not optional.

Never combine strings of unequal panel counts into one MPPT input without blocking diodes; mismatched string voltages force the higher string to backfeed the lower one.
