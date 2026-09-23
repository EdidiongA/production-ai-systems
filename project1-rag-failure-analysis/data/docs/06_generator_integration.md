# Generator and Grid Integration

Hybrid systems in weak-grid regions treat the utility and a backup generator as interchangeable AC sources. The inverter's AC input accepts one source at a time; an external automatic transfer switch (ATS) or manual changeover selects grid versus generator upstream of the inverter.

Generator sizing for battery charging: the inverter's charger draws its full configured charge current from the AC source plus any pass-through loads. A 5.5 kVA inverter charging at 80 A into 48 V (about 4.4 kW) while carrying 1.5 kW of loads needs a generator delivering 6 kW continuously. Small petrol generators are typically rated at peak; derate their nameplate by 20-25 percent for continuous duty.

Charge current limiting: set the AC charge current so generator loading stays at 70-80 percent of continuous rating. Overloaded small generators produce voltage and frequency sag that hybrid inverters reject, causing repeated disconnect-reconnect cycling.

Frequency tolerance: cheap generators drift between 48 and 53 Hz under load steps. Most hybrid inverters default to a narrow 49.5-50.5 Hz acceptance window for grid; widen the generator profile's frequency window (many firmwares expose a "generator mode") or the inverter will refuse the source.

Fuel economics in Nigeria (2025-2026): petrol generation costs roughly N350-500 per kWh delivered after fuel and maintenance, versus solar-plus-LiFePO4 lifetime cost of N80-150 per kWh, which is why solar displacement of generator runtime typically pays back in 18-30 months.
