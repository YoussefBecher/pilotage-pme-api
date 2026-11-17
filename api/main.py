import os, re, json, hmac, hashlib, datetime as dt
import jwt, psycopg, sqlglot
from sqlglot import exp
from psycopg.rows import dict_row
from fastapi import FastAPI, APIRouter, Depends, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

DB, RO, SECRET = os.environ["DATABASE_URL"], os.environ["READONLY_URL"], os.environ["JWT_SECRET"]
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
try:
    import anthropic
    AI = anthropic.Anthropic() if os.getenv("ANTHROPIC_API_KEY") else None
except ImportError:
    AI = None

app = FastAPI(title="Pilotage PME API")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS", "http://localhost:5173").split(","),
                   allow_methods=["*"], allow_headers=["*"])

def q(sql, p=None, dsn=DB):
    # Une connexion par requête : suffisant pour une démo. Pour la production, utiliser psycopg_pool.
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        return c.execute(sql, p or {}).fetchall()

# ---------- Authentification ----------
def hash_pw(pw, salt=None):
    salt = salt or os.urandom(8).hex()
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100_000).hex()

class Login(BaseModel):
    email: str
    password: str

@app.post("/api/auth/login")
def login(b: Login):
    u = q("SELECT pw, role FROM users WHERE email=%(e)s", {"e": b.email})
    if not u or not hmac.compare_digest(hash_pw(b.password, u[0]["pw"].split("$")[0]), u[0]["pw"]):
        raise HTTPException(401, "Identifiants invalides")
    exp_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)
    tok = jwt.encode({"sub": b.email, "role": u[0]["role"], "exp": exp_at}, SECRET, "HS256")
    return {"access_token": tok, "role": u[0]["role"]}

def user(authorization: str = Header("")):
    try:
        return jwt.decode(authorization.removeprefix("Bearer "), SECRET, ["HS256"])
    except Exception:
        raise HTTPException(401, "Non authentifié")

def patron(u=Depends(user)):
    if u["role"] != "patron":
        raise HTTPException(403, "Réservé au patron")
    return u

r = APIRouter(prefix="/api", dependencies=[Depends(user)])
CUR, CAT = "f.date>current_date-%(d)s", "(%(c)s='' OR p.categorie=%(c)s)"
D = Query(30, ge=1, le=365)

# ---------- Données BI (contrat attendu par l'interface React) ----------
@r.get("/kpi")
def kpi(days: int = D, categorie: str = ""):
    x = q(f"""SELECT
      COALESCE(SUM(f.montant) FILTER (WHERE {CUR}),0)::float ca, COALESCE(SUM(f.montant) FILTER (WHERE NOT {CUR}),0)::float ca_prev,
      COALESCE(SUM(f.marge) FILTER (WHERE {CUR}),0)::float marge, COALESCE(SUM(f.marge) FILTER (WHERE NOT {CUR}),0)::float marge_prev,
      COUNT(DISTINCT f.ticket) FILTER (WHERE {CUR}) tickets, COUNT(DISTINCT f.ticket) FILTER (WHERE NOT {CUR}) tickets_prev
      FROM fact_ventes f JOIN dim_produit p ON p.id=f.produit_id
      WHERE f.date>current_date-2*%(d)s AND {CAT}""", {"d": days, "c": categorie})[0]
    x["panier"] = x["ca"] / x["tickets"] if x["tickets"] else 0
    x["panier_prev"] = x["ca_prev"] / x["tickets_prev"] if x["tickets_prev"] else 0
    return x

@r.get("/ventes")
def ventes(days: int = D, categorie: str = ""):
    return q(f"""WITH j AS (SELECT f.date, SUM(f.montant) ca, COUNT(DISTINCT f.ticket) t
        FROM fact_ventes f JOIN dim_produit p ON p.id=f.produit_id
        WHERE f.date>current_date-2*%(d)s AND {CAT} GROUP BY f.date)
      SELECT to_char(d,'MM-DD') date, COALESCE(c.ca,0)::float ca, COALESCE(pv.ca,0)::float ca_prev, COALESCE(c.t,0) tickets
      FROM generate_series(current_date-(%(d)s-1), current_date, '1 day') d
      LEFT JOIN j c ON c.date=d::date LEFT JOIN j pv ON pv.date=(d::date-%(d)s) ORDER BY d""", {"d": days, "c": categorie})

@r.get("/produits")
def produits(days: int = D, categorie: str = ""):
    return q(f"""SELECT p.nom, p.categorie,
      COALESCE(SUM(f.montant) FILTER (WHERE {CUR}),0)::float ca, COALESCE(SUM(f.qte) FILTER (WHERE {CUR}),0)::int qte,
      COALESCE(ROUND(100*SUM(f.marge) FILTER (WHERE {CUR})/NULLIF(SUM(f.montant) FILTER (WHERE {CUR}),0)),0)::int marge,
      COALESCE(ROUND(100*(SUM(f.montant) FILTER (WHERE {CUR})/NULLIF(SUM(f.montant) FILTER (WHERE NOT {CUR}),0)-1)),0)::int evol
      FROM dim_produit p JOIN fact_ventes f ON f.produit_id=p.id
      WHERE f.date>current_date-2*%(d)s AND {CAT} GROUP BY p.id ORDER BY ca DESC""", {"d": days, "c": categorie})

@r.get("/categories")
def categories(days: int = D):
    return q("""SELECT p.categorie nom, ROUND(100*SUM(f.montant)/SUM(SUM(f.montant)) OVER ())::int part
      FROM fact_ventes f JOIN dim_produit p ON p.id=f.produit_id WHERE f.date>current_date-%(d)s
      GROUP BY p.categorie ORDER BY part DESC""", {"d": days})

@r.get("/stock")
def stock():
    return q("SELECT nom, stock, couverture, etat FROM v_stock ORDER BY CASE etat WHEN 'rupture' THEN 0 WHEN 'bas' THEN 1 ELSE 2 END, couverture")

@r.get("/clients")
def clients():
    rows = q("""SELECT c.id, c.nom, MAX(f.date) dernier, COUNT(DISTINCT f.ticket) achats, COUNT(DISTINCT f.ticket) FILTER (WHERE f.date>current_date-30) achats30,
      SUM(f.montant) FILTER (WHERE f.date>current_date-30)::float ca30
      FROM dim_client c JOIN fact_ventes f ON f.client_id=c.id GROUP BY c.id""")
    today, seg = dt.date.today(), {"Fidèles": 0, "Occasionnels": 0, "À risque": 0, "Perdus": 0}
    for x in rows:
        age = (today - x["dernier"]).days
        k = "Perdus" if age > 90 else "À risque" if age > 30 else "Fidèles" if x["achats"] >= 10 else "Occasionnels"
        seg[k] += 1
    n, tot = len(rows), max(len(rows), 1)
    nouveaux = q("SELECT COUNT(*) n FROM dim_client WHERE created>current_date-30")[0]["n"]
    def lbl(d): a = (today - d).days; return "hier" if a <= 1 else f"il y a {a} j"
    top = sorted([x for x in rows if x["ca30"]], key=lambda x: -x["ca30"])[:5]
    return {"kpis": [{"l": "Clients actifs", "v": str(seg["Fidèles"] + seg["Occasionnels"])}, {"l": "Nouveaux ce mois", "v": str(nouveaux)},
                     {"l": "Clients fidèles", "v": str(seg["Fidèles"])}, {"l": "Clients à risque", "v": str(seg["À risque"])}],
            "segments": [{"nom": k, "part": round(100 * v / tot)} for k, v in seg.items()],
            "top": [{"nom": x["nom"], "achats": x["achats30"], "ca": round(x["ca30"]), "dernier": lbl(x["dernier"])} for x in top]}

@r.get("/rapports", dependencies=[Depends(patron)])
def rapports():
    return [{"id": 1, "nom": "Rapport quotidien", "frequence": "Tous les jours à 08:00", "canal": "Email + WhatsApp"},
            {"id": 2, "nom": "Rapport hebdomadaire", "frequence": "Lundi à 08:00", "canal": "Email"},
            {"id": 3, "nom": "Rapport mensuel", "frequence": "1er du mois", "canal": "Email (PDF)"}]

@r.post("/rapports/{rid}/envoyer", dependencies=[Depends(patron)])
def envoyer(rid: int):
    return {"ok": True, "note": "Envoi simulé : brancher SMTP / API WhatsApp ici."}

# ---------- IA ----------
def llm(system, user_msg, max_tokens=700):
    if not AI:
        raise HTTPException(503, "Assistant IA indisponible : ANTHROPIC_API_KEY manquante")
    res = AI.messages.create(model=MODEL, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user_msg}])
    return res.content[0].text

SCHEMA = """Base PostgreSQL en lecture seule (devise : DT) :
fact_ventes(id, date date, ticket int, produit_id, client_id, qte int, montant numeric, marge numeric)
dim_produit(id, nom, categorie, prix_achat) ; catégories : Épicerie, Boissons, Hygiène, Produits frais
dim_client(id, nom, created date)
v_stock(id, nom, stock int, couverture float (jours), etat text : ok | bas | rupture)
Jointures : fact_ventes.produit_id=dim_produit.id ; fact_ventes.client_id=dim_client.id ; v_stock.id=dim_produit.id."""
ALLOWED = {"fact_ventes", "dim_produit", "dim_client", "v_stock"}

def safe_sql(sql):
    """Garde-fou n°1 : une seule requête SELECT, tables autorisées uniquement."""
    try:
        tree = sqlglot.parse(sql, read="postgres")
    except Exception:
        raise HTTPException(422, "Requête SQL illisible")
    if len(tree) != 1 or not isinstance(tree[0], exp.Select):
        raise HTTPException(422, "Seules les requêtes SELECT sont autorisées")
    ctes = {c.alias for c in tree[0].find_all(exp.CTE)}
    for t in tree[0].find_all(exp.Table):
        if t.name not in ALLOWED | ctes:
            raise HTTPException(422, f"Table non autorisée : {t.name}")
    if re.search(r"\bpg_|information_schema|\bset\s", sql, re.I):
        raise HTTPException(422, "Requête refusée")
    return tree[0].sql(dialect="postgres")

class Ask(BaseModel):
    question: str

USAGE = {}  # compteur en mémoire par utilisateur et par jour (à remplacer par Redis en production)

@r.post("/chat")
def chat(b: Ask, u=Depends(user)):
    key = (u["sub"], dt.date.today())
    USAGE[key] = USAGE.get(key, 0) + 1
    if USAGE[key] > int(os.getenv("CHAT_LIMIT", "50")):
        raise HTTPException(429, "Limite quotidienne atteinte")
    out = llm(f"Tu es analyste BI. Date du jour : {dt.date.today()}.\n{SCHEMA}\n"
              'Réponds UNIQUEMENT par un JSON {"sql":"..."} contenant une seule requête SELECT PostgreSQL, '
              "avec des alias de colonnes en français lisibles. Aucun commentaire.", b.question[:500])
    try:
        sql = json.loads(re.sub(r"^```(?:json)?|```$", "", out.strip(), flags=re.M).strip())["sql"]
    except Exception:
        raise HTTPException(422, "Réponse IA invalide, reformulez la question")
    sql = safe_sql(sql)
    try:
        rows = q(f"SELECT * FROM ({sql}) t LIMIT 200", dsn=RO)  # Garde-fou n°2 : rôle PostgreSQL lecture seule + timeout 5 s
    except psycopg.Error:
        raise HTTPException(422, "La requête générée a échoué, reformulez la question")
    rep = llm("Réponds en français en 1 à 2 phrases, factuel, chiffres en DT. N'invente rien.",
              f"Question : {b.question}\nRésultat (20 premières lignes) : {json.dumps(rows[:20], default=str, ensure_ascii=False)}", 300)
    return {"sql": sql, "colonnes": list(rows[0]) if rows else [], "lignes": rows, "reponse": rep}

CACHE = {}

@r.get("/resume", dependencies=[Depends(patron)])
def resume():
    key = dt.datetime.now().strftime("%Y%m%d%H")  # un résumé par heure
    if key in CACHE:
        return CACHE[key]
    k, top, rup = kpi(7, ""), produits(7, "")[:3], [s["nom"] for s in stock() if s["etat"] == "rupture"]
    data = {"kpi_7j": k, "top_produits": [(p["nom"], p["ca"], p["evol"]) for p in top], "ruptures": rup}
    try:
        txt = llm("Tu es conseiller de gestion pour un commerçant. Écris 3 phrases en français : ce qui progresse, "
                  "ce qui baisse, et UNE action concrète. Utilise uniquement les chiffres fournis, en DT.",
                  json.dumps(data, default=str, ensure_ascii=False), 300)
    except HTTPException:
        d = (k["ca"] / k["ca_prev"] - 1) * 100 if k["ca_prev"] else 0
        txt = f"Sur 7 jours, le CA est de {k['ca']:.0f} DT ({d:+.1f} % vs période précédente). " + \
              (f"Rupture : {', '.join(rup)}." if rup else "Aucune rupture de stock.")
    CACHE.clear()
    CACHE[key] = {"texte": txt}
    return CACHE[key]

app.include_router(r)

_llm = llm
def llm(system, user_msg, max_tokens=700):
    try:
        return _llm(system, user_msg, max_tokens)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Assistant IA indisponible : vérifiez ANTHROPIC_API_KEY")
