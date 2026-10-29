"""
Análisis de cobertura: qué títulos del 597 ya podemos valorar y cuáles faltan.
Genera una tabla resumen por tipo de instrumento.

Ejecutar: python scripts/cobertura_valoracion.py
"""
import pandas as pd
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from calcular_encaje import cargar_portafolio

# Cargar portafolio
df = cargar_portafolio()

# Limpiar campos
df["Moned"] = df["Moned"].astype(str).str.strip()
df["Facial_str"] = df["Facial"].astype(str).str.strip()
df["Especie_str"] = df["Especie"].astype(str).str.strip()
df["Mod_str"] = df["Mod"].astype(str).str.strip()

# Clasificar cada título
def clasificar(row):
    moned = row["Moned"]
    facial = row["Facial_str"]
    especie = row["Especie_str"]
    mod = row["Mod_str"]

    # Acciones
    if "ACC" in especie or "ECOPETROL" in especie or "iShares" in especie or "ACWV" in especie or "USMV" in especie:
        return "ACCIONES/ETF"
    if "AO " in especie or especie.startswith("AO"):
        return "ACCIONES/ETF"

    # TIPS
    if "TIPS" in especie:
        if moned == "UVR":
            return "TIPS_UVR"
        return "TIPS_COP"

    # TES UVR
    if moned == "UVR":
        return "TES_UVR"

    # IPC
    if "IPC" in facial:
        return "IPC_CORP"

    # IBR
    if "IBR" in facial:
        return "IBR"

    # DTF
    if "DTF" in facial:
        return "DTF"

    # Tasa Fija COP (TES y corporativos)
    if moned == "$" and facial.replace(".", "").replace("-", "").replace("No", "").strip()[:1].isdigit():
        if "DIR CREDITO PUB" in str(row.get("Emisor", "")) or "NACION" in str(row.get("Calif.", "")):
            return "TES_TF"
        if mod == "MV":
            return "TIPS_COP"  # Mes Vencido con tasa fija = TIPS
        return "TF_CORP"

    # USD
    if moned in ("US$", "USD"):
        return "USD"

    # No aplica / Otros
    if "No aplica" in facial or facial == "nan":
        if "ACC" in especie or "iShares" in especie:
            return "ACCIONES/ETF"
        return "OTRO"

    # Time deposits
    if "TIME DEP" in especie:
        return "TIME_DEPOSIT"

    return "OTRO"

df["Tipo_Valoracion"] = df.apply(clasificar, axis=1)

# Valor de mercado numérico
df["VM"] = df["Valor_Mercado"]

# Cobertura: qué ya tenemos función
CUBIERTOS = {
    "TES_TF": "valorar_bono_tf() — NL/365 ✅",
    "TES_UVR": "valorar_bono_uvr() — NL/365 ✅",
    "IPC_CORP": "valorar_bono_ipc() — 30/360 ✅",
    "TIPS_COP": "sensibilizar_tips() — NL/365 ✅",
    "TIPS_UVR": "sensibilizar_tips() — NL/365 ✅",
}

PENDIENTES = {
    "TF_CORP": "Similar a TES_TF pero podría ser 30/360",
    "IBR": "Bono flotante — necesita proyección IBR",
    "DTF": "Bono flotante — necesita proyección DTF",
    "ACCIONES/ETF": "No se valora por flujos — usar precio de mercado",
    "USD": "Bonos/ETFs en USD — necesita TRM + precio",
    "TIME_DEPOSIT": "CDT/Depósito a plazo — valorar como zero coupon",
    "OTRO": "Clasificación manual requerida",
}

# Resumen
resumen = (
    df.groupby("Tipo_Valoracion")
    .agg(
        Titulos=("Especie_str", "count"),
        VM_Total=("VM", "sum"),
        Especies_Unicas=("Especie_str", "nunique"),
    )
    .reset_index()
    .sort_values("VM_Total", ascending=False)
)

resumen["VM_MM"] = resumen["VM_Total"] / 1e6
resumen["Estado"] = resumen["Tipo_Valoracion"].map(
    lambda x: "✅ CUBIERTO" if x in CUBIERTOS else "❌ PENDIENTE"
)
resumen["Funcion/Nota"] = resumen["Tipo_Valoracion"].map(
    lambda x: CUBIERTOS.get(x, PENDIENTES.get(x, "?"))
)

# Imprimir
print("=" * 100)
print("COBERTURA DE VALORACIÓN — PORTAFOLIO 597")
print("=" * 100)
print()

total_vm = resumen["VM_Total"].sum()
vm_cubierto = resumen[resumen["Estado"] == "✅ CUBIERTO"]["VM_Total"].sum()
vm_pendiente = resumen[resumen["Estado"] == "❌ PENDIENTE"]["VM_Total"].sum()

print(f"  VM Total portafolio:  ${total_vm/1e9:,.1f} MM")
print(f"  VM Cubierto:          ${vm_cubierto/1e9:,.1f} MM ({vm_cubierto/total_vm*100:.1f}%)")
print(f"  VM Pendiente:         ${vm_pendiente/1e9:,.1f} MM ({vm_pendiente/total_vm*100:.1f}%)")
print()
print("-" * 100)
print(f"{'Tipo':<16} {'Estado':<14} {'Títulos':>8} {'Especies':>9} {'VM (MM COP)':>14} {'% Port':>7}  Función/Nota")
print("-" * 100)

for _, row in resumen.iterrows():
    print(f"{row['Tipo_Valoracion']:<16} {row['Estado']:<14} {row['Titulos']:>8,} {row['Especies_Unicas']:>9} "
          f"{row['VM_MM']:>14,.0f} {row['VM_Total']/total_vm*100:>6.1f}%  {row['Funcion/Nota']}")

print("-" * 100)
print()

# Detalle de pendientes
print("=" * 100)
print("DETALLE DE TIPOS PENDIENTES")
print("=" * 100)

for tipo in PENDIENTES:
    df_tipo = df[df["Tipo_Valoracion"] == tipo]
    if df_tipo.empty:
        continue
    
    vm_tipo = df_tipo["VM"].sum()
    n = len(df_tipo)
    especies = df_tipo["Especie_str"].unique()[:5]
    
    print(f"\n{'─'*60}")
    print(f"  {tipo} — {n} títulos — ${vm_tipo/1e6:,.0f} MM")
    print(f"  Nota: {PENDIENTES[tipo]}")
    print(f"  Ejemplos: {', '.join(especies[:3])}")
    if len(especies) > 3:
        print(f"            ... y {len(df_tipo['Especie_str'].unique())-3} especies más")
