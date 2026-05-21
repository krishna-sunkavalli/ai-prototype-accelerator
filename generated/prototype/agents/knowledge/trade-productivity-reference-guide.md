# Trade Productivity Reference Guide

**Organization:** ACCO Engineered Systems
**Industry:** Mechanical Construction & Facility Services
**Last Updated:** May 18, 2026
**Classification:** Internal Use

---

## Overview

The Trade Productivity Reference Guide publishes the standard labor
productivity baselines and crew compositions ACCO Engineered Systems uses
when estimating self-perform mechanical work across our western US service
area. The values in this guide are the result of more than 30 years of
post-job analysis on data center, healthcare, pharmaceutical, life
sciences, sports and entertainment, commercial, and industrial projects.

Productivity baselines in this guide represent the hours a competent
journeyman crew is expected to spend installing one unit of work under
normal conditions: 5-day work week, single-shift, ambient temperatures,
moderate floor congestion, and standard material delivery. Each baseline
is multiplied by a project-specific *productivity factor* that the
estimator chooses based on real conditions on the job.

## 1. Sheet Metal Productivity

Sheet metal is the largest single-trade scope on most ACCO bids. Baseline
units are hours per linear foot of installed ductwork at the listed gauge.

| Duct type                | Size band           | Baseline hours per LF |
|--------------------------|---------------------|-----------------------|
| Rectangular galvanized   | Up to 24"           | 0.30                  |
| Rectangular galvanized   | 25"–48"             | 0.45                  |
| Rectangular galvanized   | 49"–72"             | 0.70                  |
| Round galvanized         | Up to 14"           | 0.18                  |
| Round galvanized         | 16"–36"             | 0.30                  |
| Stainless steel exhaust  | Any                 | 0.95                  |
| FRP exhaust              | Any                 | 1.10                  |
| Flexible duct (lined)    | Any                 | 0.12                  |

Add 15% to the baseline for any installation above 16 feet AFF that
requires a scissor lift, and 30% for boom-lift installation above 30 feet.
Hospital and pharmaceutical scopes add 10% for in-line inspection and
documentation overhead.

## 2. Piping Productivity

Piping baselines are hours per linear foot of installed pipe including
hangers, supports, and standard fittings. They assume schedule 40 steel
unless otherwise noted.

| Pipe service               | Size band     | Baseline hours per LF |
|----------------------------|---------------|-----------------------|
| Chilled water (steel)      | 1"–2"         | 0.35                  |
| Chilled water (steel)      | 3"–6"         | 0.60                  |
| Chilled water (steel)      | 8"–12"        | 1.10                  |
| Hot water (steel)          | 1"–2"         | 0.40                  |
| Steam (sch 80)             | 2"–6"         | 0.80                  |
| Condensate (copper)        | 1"–2"         | 0.30                  |
| Refrigerant (copper)       | 3/8"–1-5/8"   | 0.45                  |
| Stainless process (BPE)    | 1"–4"         | 1.40                  |
| Medical gas (NFPA 99)      | 1/2"–2"       | 0.85                  |

Welded joints add 1.5 hours per joint for carbon steel and 3.0 hours per
joint for stainless ortbital welding. Add 25% for any scope inside a
cleanroom envelope. Add 40% for any scope inside an operating data hall
under live-load conditions.

## 3. Plumbing Productivity

Plumbing baselines are hours per fixture or hours per linear foot of
distribution piping.

| Element                          | Baseline hours        |
|----------------------------------|-----------------------|
| Standard water closet            | 4.5 hr per fixture    |
| Lavatory                         | 3.0 hr per fixture    |
| Service sink                     | 5.0 hr per fixture    |
| Domestic water distribution 1"   | 0.20 hr per LF        |
| Drain, waste, vent (cast iron)   | 0.55 hr per LF        |
| Roof drain assembly              | 6.0 hr per drain      |
| Backflow preventer (2")          | 5.5 hr per device     |

Healthcare scopes add 20% for sensor faucets, antimicrobial coatings, and
hospital-grade documentation. Hospitality high-rise scopes add 10% for
vertical riser logistics.

## 4. Controls Installation

Controls work is sequenced after the trades it controls. Baseline hours
include point installation, low-voltage wiring, programming, and graphics.

| Element                          | Baseline hours        |
|----------------------------------|-----------------------|
| VAV box w/ reheat (BMS point)    | 8.0 hr per box        |
| AHU integration (per AHU)        | 40 hr per AHU         |
| Chiller plant graphics + tuning  | 120 hr per plant      |
| Cleanroom pressure cascade       | 14 hr per zone        |
| BMS-to-EPMS integration          | 200 hr per project    |

Mission-critical data center sequences add a 25% productivity factor and
must include a redundant controller test plan budgeted at 80 hours.

## 5. Productivity Factor Selection

The productivity factor (PF) is the single most important number on a bid.
Choose PF based on real conditions.

| Condition                                         | PF range  |
|---------------------------------------------------|-----------|
| Brand-new construction, single shift, daytime     | 0.90–1.00 |
| Renovation in occupied space                      | 1.10–1.25 |
| Live data center hot-aisle work                   | 1.30–1.50 |
| Hospital occupied wing                            | 1.20–1.35 |
| Stadium event-day blackout work                   | 1.40–1.60 |
| Cleanroom build-out (gowning, environmental)      | 1.25–1.45 |
| Off-shift / overtime mandated                     | 1.15–1.30 |
| Severe weather exposure                           | 1.10–1.20 |

PF below 0.90 requires written justification from the Chief Estimator.

## 6. Crew Composition

| Crew                | Composition                                  |
|---------------------|----------------------------------------------|
| Standard sheet metal| 1 foreman + 2 journeymen + 1 apprentice      |
| Standard piping     | 1 foreman + 2 pipefitters + 1 welder + 1 helper |
| Plumbing            | 1 foreman + 2 plumbers + 1 apprentice        |
| Controls            | 1 lead technician + 1 technician + 1 programmer (shared) |
| Commissioning       | 1 commissioning agent + 1 T&B technician      |

Increase crew size when the schedule mandates parallel work fronts; never
exceed three crews per work area without coordination with the General
Contractor.

## 7. Regional Adjustments

Regional cost-of-labor differences are captured separately in the
`labor_rates` database. Productivity in this guide is region-neutral —
estimators apply hourly rates from the database after computing crew hours.

## 8. Document Control

This guide is owned by the Chief Estimator and updated each January after
the prior-year post-job productivity audit. Suggested revisions are
submitted to the Pre-Construction shared library.
