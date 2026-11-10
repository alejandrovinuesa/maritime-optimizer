# Datos regulatorios — Tablas del marco normativo

Esta carpeta contiene las tablas regulatorias del Capítulo 2 del TFG en
formato CSV, separadas del código Python para facilitar su revisión,
auditoría y actualización sin necesidad de tocar la lógica del programa.

Cada CSV tiene **una fuente normativa única y trazable** que se documenta
abajo. Si una autoridad publica una versión actualizada (por ejemplo,
nuevos factores Z para CII después de la revisión IMO 2026), basta con
modificar el CSV correspondiente; el código `config.py` los lee
automáticamente.

## Mapa de archivos

| Archivo CSV | Tabla TFG | Fuente normativa | Uso en el modelo |
|-------------|-----------|------------------|------------------|
| `emission_factors_ttw.csv` | Tabla 2.1 | Reg. Delegado (UE) 2023/2776, Anexo I | Cálculo de emisiones MRV (Ec. 2.2–2.4) y EU ETS (Ec. 3.3) |
| `lng_engine_slip.csv` | Texto §2.1.3 | Reg. Delegado (UE) 2023/2776, Anexo I | Coeficientes C_slip por tipo de motor (CH₄ slip) |
| `emission_factors_wtt.csv` | Tabla 2.3 | Reg. (UE) 2023/1805 (FuelEU Maritime), Anexo II | Cálculo intensidad WtW (Ec. 2.8–2.9) y G_VEP de ISO 14083 (Ec. 3.10) |
| `fueleu_targets.csv` | Tabla 2.4 | Reg. (UE) 2023/1805, art. 4 | Restricción R5 sobre intensidad GHGI (Ec. 3.8) |
| `cii_reference_params.csv` | — | Resolución MEPC.353(78), G2 | Línea de referencia CII (Ec. 2.14) |
| `cii_reduction_factors.csv` | — | Resolución MEPC.338(76) | Factor Z anual del CII requerido |
| `cii_rating_thresholds.csv` | Tabla 2.5 (umbrales) | Resolución MEPC.354(78), Tabla 5 | Asignación de rating A–E, restricción R3 |
| `fuel_prices.csv` | — | Datos de mercado (escenario base 2026+) | Precios de combustible para coste C_fuel (Ec. 3.2) |

> **Nota**: la herramienta opera desde 2026 en adelante, por lo que el
> phase-in del EU ETS (40 % en 2024, 70 % en 2025) **no se modela**:
> el factor de cobertura es siempre 1.00 desde 2026. Por eso no hay un
> CSV de phase-in en este directorio.

## Notas sobre los datos

### `emission_factors_ttw.csv`

Factores tank-to-wake del Reglamento Delegado (UE) 2023/2776, aplicables
al cálculo de emisiones MRV (fórmulas 2.1–2.4 del TFG). Para el GNL,
el coeficiente `c_slip` mostrado es el valor por defecto (Otto velocidad
baja dual, 1.7 %); los valores específicos por motor están en
`lng_engine_slip.csv`. Las celdas vacías en `ef_ch4` y `ef_n2o` para
el metanol corresponden a los valores marcados como **PdM (Pendiente de
Medición)** en el reglamento.

La columna `zero_rating_eligible` indica si el combustible puede aplicar
EF*_CO₂ = 0 cuando cumple los criterios RED II (Restricción R4 / Ec. 3.7
del TFG). Es elegibilidad técnica; la naviera debe acreditar
adicionalmente el balance de masas verificable.

### `emission_factors_wtt.csv`

Factores well-to-tank del Anexo II del FuelEU Maritime. Incluye:
- **`lcv_mj_g`**: Poder Calorífico Inferior en MJ/g (cuidado con la unidad
  — algunas tablas usan MJ/kg)
- **`wtt_co2eq_g_mj`**: emisiones upstream en gCO₂eq/MJ
- **`cf_*`**: factores de conversión TtW por gas (idénticos a Tabla 2.1)
- **`c_slip_pct`**: fuga de metano por combinación combustible-motor

Los valores marcados como `E_minus_Cf_LCV` o `Ref_RED_II` indican que el
WtT depende del lote certificado del biocombustible/RFNBO (RED II). Para
la implementación se usan valores de referencia razonables; para uso real
en la naviera se deben tomar los datos de los certificados de cada
suministro.

### `cii_*.csv` (tres archivos relacionados)

El cálculo del CII requerido sigue la cadena:

```
CII_ref(DWT) = a × DWT^(-c)              [cii_reference_params.csv]
CII_req(year) = CII_ref × (1 - Z(year)/100)   [cii_reduction_factors.csv]
Rating = compare(CII_alcanzado / CII_req, d-vectors)  [cii_rating_thresholds.csv]
```

**Aviso importante**: los factores Z de los años 2027–2030 están como
"placeholder" igual al valor de 2026 (11 %), porque la OMI tiene previsto
revisarlos en 2026. Cuando se publiquen los nuevos valores, basta con
actualizar este CSV.

### `fueleu_targets.csv`

La fila `2050,9999,80.0,18.23` cubre los años 2050 en adelante. El año
final 9999 es solo un sentinela para que la lógica de búsqueda por rangos
(`period_start <= year <= period_end`) funcione sin casos especiales.

### `fuel_prices.csv`

**Estos NO son datos regulatorios**. Son precios de mercado de referencia
para los escenarios de demostración. Volátiles por naturaleza; deben
actualizarse al ejecutar el modelo en producción.

## Para contribuidores

Si vas a modificar los datos:

1. **No modifiques los CSV regulatorios sin contrastar la fuente.** Cada
   archivo tiene la regulación citada arriba — abre el DOUE / Resolución
   MEPC correspondiente antes de tocar valores.

2. **Si una regulación se actualiza** (por ejemplo, factores Z del CII tras
   la revisión IMO 2026), modifica solo el CSV. `config.py` y los módulos
   `emissions.py` / `optimizer.py` no necesitan cambios.

3. **Para añadir un combustible nuevo** (p.ej. amoníaco verde):
   - Añade fila en `emission_factors_ttw.csv` con sus factores TtW
   - Añade fila en `emission_factors_wtt.csv` con su LCV y WtT
   - Añade fila en `fuel_prices.csv` con su precio
   - Añade el código en el enum `FuelType` de `models.py`

4. **`fuel_prices.csv` es el único que se actualiza con frecuencia**.
   Los demás cambian solo cuando hay revisión normativa (raras).
