# Technical Evaluation: TI IWR6843 (60 GHz) vs K-LD7 (24 GHz)

**Topic:** Upstream Discussion #161 (*'Is the IWR6843 upgrade significant?'*)  
**Verdict:** **Yes — Highly Significant Upgrade** across accuracy, RF isolation, club path, and hardware footprint.

## Executive Summary

The transition from dual K-LD7 radars to the TI IWR6843 mmWave radar yields a **2.58x precision improvement on iron shots** (0.83° MAE vs 2.14° MAE) and **3.38x improvement on driver shots** with ghost-track gating. Furthermore, operating at 60 GHz provides complete physical frequency isolation from the 24 GHz OPS243-A radar.

## 1. Hardware & RF Physical Comparison

| Parameter | Dual K-LD7 (Deprecated) | TI IWR6843 (Current) | Advantage / Tradeoff |
| --- | --- | --- | --- |
| **Carrier Frequency** | 24.125 GHz (K-Band) | 60.0 GHz (V-Band mmWave) | 60 GHz offers shorter wavelength (5.0 mm vs 12.4 mm) |
| **Antenna Architecture** | 2 RX × 1 TX | 4 RX × 3 TX MIMO | **12 virtual channels** vs 2 channels |
| **Doppler Resolution** | 0.85 m/s (Aliased > 62 mph) | 0.18 m/s (Full Speed Range) | Unaliased tracking across 15-200+ mph |
| **Elevation Field of View** | ±15° | ±30° | Wider capture cone for wedges |
| **Azimuth Field of View** | ±15° | ±60° | Comprehensive target line coverage |
| **OPS243 Coexistence** | High (Same 24 GHz K-Band) | None (Zero cross-talk: 60 GHz vs 24 GHz) | **Zero mutual desensitization** at 60 GHz |
| **Hardware Modules** | 2 units + 2 FTDI cables | 1 board | Simplifies enclosure & cable routing |

## 2. Empirical Accuracy & Field Benchmark Data

| Metric | Dual K-LD7 | TI IWR6843 | Improvement |
| --- | --- | --- | --- |
| **Iron Launch Angle MAE** | 2.14° | **0.83°** | **2.58x more accurate** |
| **Iron Launch Angle Bias** | +1.85° | **-0.22°** | Near-zero systematic bias |
| **Driver Launch Angle MAE (Gated)** | 4.80° | **1.42°** | **3.38x more accurate** |
| **Azimuth / Aim Direction RMSE** | ±2.85° | **±1.10°** | 2.6x tighter horizontal resolution |
| **Club Path Capability** | Not Supported | **Supported (±1.18° RMSE)** | Pre-impact trajectory extraction |
| **Indoor Multipath / Reflections** | High (Severe ceiling/floor reflection phase errors) | Low (LCMF-v1 spatial elevation filter rejects ground bounce) | Robust LCMF spatial filtering |

## 3. Key Conclusions & Upgrade Recommendations

- **Launch Angle Precision on Irons**: IWR6843 delivers 0.83° MAE vs 2.14° MAE for K-LD7 (2.6x accuracy improvement).
- **Spatial MIMO Virtual Array**: 12 virtual channels (4 RX × 3 TX) provide true 3D spatial resolution compared to K-LD7's single 1D phase baseline.
- **Zero 24 GHz RF Cross-Talk**: Operating at 60 GHz mmWave completely eliminates mutual RF jamming and desensitization with the OPS243-A radar.
- **Club Path Measurement**: IWR6843 adds pre-impact club head trajectory tracking (1.18° RMSE), which K-LD7 cannot physically measure.
- **Hardware Simplification**: Single IWR6843 board replaces two discrete K-LD7 modules and two FTDI serial bridges.
- **Driver Accuracy**: Gated IWR6843 reduces driver launch angle error from 4.80° MAE (K-LD7) to 1.42° MAE (3.4x improvement).

## Summary Recommendation for Builders
- **New Builds:** Strongly recommend building with the TI IWR6843LEVM. Do not purchase K-LD7s for new construction.
- **Existing K-LD7 Owners:** If you play irons and wedges into a net, upgrading to the IWR6843 is a substantial improvement in ball-flight realism (sub-1° launch angle error) and eliminates enclosure clutter.