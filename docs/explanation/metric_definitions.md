# Weather Dashboard Metric Definitions & Calculations

This document defines the mathematical, physical, and computational specifications for all weather metrics surfaced by the API (`/api/stats`, `/api/readings`, `/api/stream`) and rendered on the dashboard.

---

## 1. Primary Ingested Metrics

The ingestion pipeline receives physical environmental measurements recorded by weather stations:
* **Temperature ($T$):** Ambient air temperature measured in degrees Celsius (°C).
* **Relative Humidity ($RH$):** Ratio of partial vapor pressure to equilibrium vapor pressure, expressed as a percentage ($0\% - 100\%$).
* **Wind Speed ($V$):** Horizontal air velocity measured in meters per second (m/s).

*Note: In accordance with data integrity guidelines, unavailable physical sensor readings such as rainfall, solar irradiance, or wind direction are strictly not invented or estimated.*

---

## 2. Statistical Aggregations & Distinctions

For each metric in a designated time window ($[T_{\text{start}}, T_{\text{end}}]$), the system computes:

### 2.1 Latest Value & Reading Age
* **Latest Value:** The most recent recorded observation chronologically within the filtered window.
* **Reading Age:** Elapsed duration in seconds from the most recent observation's UTC timestamp to the current reference UTC time:
  $$\text{Reading Age} = t_{\text{ref}} - t_{\text{latest}}$$
  Formatted human-readably on the UI (e.g., `45s ago`, `12m ago`, `2h ago`).

### 2.2 Sample Mean (Arithmetic Average)
The unweighted arithmetic mean across all discrete samples within the window:
$$\bar{x}_{\text{sample}} = \frac{1}{N} \sum_{i=1}^{N} x_i$$
*Use case:* Reflects the average sensor transmission value regardless of sample timing.

### 2.3 Time-Weighted Average (TWA)
Continuous physical phenomena often experience irregular sample intervals (e.g., station backoff, network disconnects, burst transmissions). An unweighted sample mean over-indexes on burst transmissions.

The Time-Weighted Average integrates observations across time using trapezoidal interpolation:
$$\bar{x}_{\text{TWA}} = \frac{1}{t_N - t_1} \sum_{i=1}^{N-1} \left( \frac{x_i + x_{i+1}}{2} \right) \cdot (t_{i+1} - t_i)$$
* **Identical timestamps:** Consecutive points with $t_{i+1} = t_i$ contribute zero interval duration and avoid zero-division.
* **Single point / zero total elapsed time:** Uses the single point's value, or the sample mean when multiple points share one timestamp.

### 2.4 Period-Over-Period Change
Compares the current window against an immediately preceding equal-duration window $[T_{\text{start}} - \Delta t, T_{\text{start}}]$:
$$\Delta \bar{x} = \bar{x}_{\text{current}} - \bar{x}_{\text{previous}}$$

#### Explicit Comparison Availability & Reason Labels:
Comparisons are marked unavailable with clear, documented reasons:
* `range="all"`: Historical dataset spans everything; no preceding time window exists (`"All history selected (no preceding baseline window)"`).
* `range="limit"`: Count-based limits (e.g., last 50 readings) have variable temporal spans (`"Unavailable for record-count limits (variable time duration)"`).
* `empty preceding period`: The preceding equal-duration period contains zero readings (`"No telemetry in preceding window"`).

---

## 3. Calculated Metric: Dew Point ($T_d$)

Dew point is the temperature to which air must be cooled at constant pressure to become saturated with water vapor.

### 3.1 Magnus-Tetens Formulation
Dew point is derived from paired temperature ($T$, °C) and relative humidity ($RH$, %) readings using the Magnus-Tetens approximation:

$$\gamma(T, RH) = \frac{a \cdot T}{b + T} + \ln\left(\frac{RH}{100}\right)$$

$$T_d = \frac{b \cdot \gamma(T, RH)}{a - \gamma(T, RH)}$$

* **Magnus Constants:**
  * $a = 17.27$
  * $b = 237.7^\circ\text{C}$

### 3.2 Validity Input Bounds & Safeguards
* Temperature validity domain: $-10^\circ\text{C} \le T \le 40^\circ\text{C}$.
* Relative humidity validity domain: $0\% < RH \le 100\%$.
* At $100\%$ RH, $T_d = T$.
* For non-positive RH ($RH \le 0$), out-of-range temperatures, or missing/null inputs, the calculation returns `None` without polluting aggregations.
* Clearly labeled on both API and UI as **Calculated** (`(Calculated)` badge).
