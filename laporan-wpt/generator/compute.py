import math
from statistics import mean

DATA = {
    "silinder": {
        "Diameter": [3.98, 4.00, 3.97, 3.99, 3.96],
        "Tinggi":   [6.50, 6.50, 6.50, 6.49, 6.51],
    },
    "balok": {
        "Panjang": [9.8, 9.7, 9.5, 9.4, 9.5],   # ke-5 pada lembar mentah tertulis 9,8; analisis lembar data memakai 9,5
        "Tinggi":  [2.3, 2.4, 2.3, 2.3, 2.3],
        "Lebar":   [6.1, 6.1, 5.9, 6.0, 6.0],
    },
    "plat": {
        "Panjang": [16.5, 16.6, 16.5, 16.5, 16.6],
        "Tinggi":  [0.15, 0.15, 0.14, 0.14, 0.13],
        "Lebar":   [8.1, 8.3, 8.2, 8.3, 8.2],
    },
}

def stats(xs):
    n = len(xs)
    s = sum(xs)
    xb = s / n
    dev2 = [(x - xb) ** 2 for x in xs]
    S = math.sqrt(sum(dev2) / (n - 1))          # pers. 4 modul (deviasi standar sampel)
    return dict(n=n, sum=s, mean=xb, dev2=dev2, sumdev2=sum(dev2), S=S, rel=S / xb * 100)

def cyl_volume(D, T, dD, dT):
    V = 0.25 * math.pi * D**2 * T
    dVdD = 0.5 * math.pi * D * T
    dVdT = 0.25 * math.pi * D**2
    dV = math.sqrt((dVdD * dD)**2 + (dVdT * dT)**2)
    return V, dV, dVdD, dVdT

def box_volume(P, L, T, dP, dL, dT):
    V = P * L * T
    dV = math.sqrt((L*T*dP)**2 + (P*T*dL)**2 + (P*L*dT)**2)
    return V, dV

if __name__ == "__main__":
    R = {k: {q: stats(v) for q, v in d.items()} for k, d in DATA.items()}
    for k, d in R.items():
        for q, st in d.items():
            print(f"{k:9s} {q:9s} sum={st['sum']:.4f} mean={st['mean']:.4f} sumdev2={st['sumdev2']:.6f} S={st['S']:.7f} rel={st['rel']:.3f}%")
    s = R["silinder"]; V, dV, a, b = cyl_volume(s["Diameter"]["mean"], s["Tinggi"]["mean"], s["Diameter"]["S"], s["Tinggi"]["S"])
    print(f"V sil = {V:.5f} ± {dV:.5f}  ({dV/V*100:.3f}%)  dV/dD={a:.5f} dV/dT={b:.5f}")
    b_ = R["balok"]; V, dV = box_volume(b_["Panjang"]["mean"], b_["Lebar"]["mean"], b_["Tinggi"]["mean"], b_["Panjang"]["S"], b_["Lebar"]["S"], b_["Tinggi"]["S"])
    print(f"V balok = {V:.6f} ± {dV:.5f} ({dV/V*100:.3f}%)")
    p = R["plat"]; V, dV = box_volume(p["Panjang"]["mean"], p["Lebar"]["mean"], p["Tinggi"]["mean"], p["Panjang"]["S"], p["Lebar"]["S"], p["Tinggi"]["S"])
    print(f"V plat = {V:.7f} ± {dV:.5f} ({dV/V*100:.3f}%)")
