# Hôtel et Restaurant — gestion hôtelière (V1)

Django 5 + SQLite/PostgreSQL. Interface responsive, thème clair/sombre, temps réel par polling
(compatible PythonAnywhere). Aucune dépendance JavaScript externe.

## Sans rien installer : GitHub Codespaces
1. Créez un dépôt GitHub et envoyez-y le contenu du dossier `hotel` (bouton *Add file → Upload files*).
2. Sur la page du dépôt : **Code → Codespaces → Create codespace on main**.
3. Attendez 2 à 3 minutes : l'application s'installe et s'ouvre seule dans un nouvel onglet
   (sinon, onglet *Ports* → lien du port 8000). Connexion : `rece1` / `hotelrest2026`.
   Administration `/admin/` : compte `proprio` / `hotelrest2026`.

## Démarrage local
```bash
python -m venv venv && source venv/bin/activate      # Windows : venv\Scripts\activate
pip install -r requirements.txt
python manage.py makemigrations accounts hotels audit reservations frontdesk billing portal kitchen
python manage.py migrate
python manage.py seed_demo          # hôtel de démonstration + comptes
python manage.py createsuperuser    # pour /admin/
python manage.py runserver
```
Comptes de démo (mot de passe `hotelrest2026`) : `proprio`, `gerant`, `rece1`, `rece2`, `menage1`, `cuisto1`.
Portail public : `/h/vanille/` · Administration (chambres, formules, réglages) : `/admin/`.

> `makemigrations` est à lancer une fois : les migrations ne sont pas incluses.

## Déploiement PythonAnywhere (prototype)
1. Uploadez le dossier, créez un virtualenv, `pip install -r requirements.txt`.
2. Onglet *Web* : WSGI → `config.wsgi`, variables : `SECRET_KEY`, `DEBUG=0`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`.
3. Fichiers statiques : `/static/` → `.../staticfiles` (après `python manage.py collectstatic`), `/media/` → `.../media`.
4. `migrate`, `seed_demo` (facultatif).
Le tableau de bord se rafraîchit toutes les 8 s ; les alertes sont calculées à chaque rafraîchissement
(aucune tâche de fond n'est nécessaire). Pour de vrais hôtels : VPS + PostgreSQL (voir `.env.example`).

## Ce que contient la V1
- Multi-hôtels avec isolation stricte, rôles et permissions (`hotels/permissions.py`)
- Formules paramétrables (1H, 2H, Journée, Nuitée, 24H) et moteur de disponibilité sans chevauchement,
  remise en état automatique entre séjours, dépassements bloquants
- Réservations : circuit En attente → Acceptée → Confirmée, appel H-1, dernière minute, no-show automatique
  des injoignables, annulation avec motif obligatoire
- Check-in avec identité complète (photo de pièce, signature), check-out horodaté serveur,
  départ anticipé sans remboursement, prolongation facturée jamais automatique
- Facturation : folio, statut par ligne, 4 modes de paiement, remboursement validé par le gérant
- Quarts : comptage à l'aveugle, passation signée, versement au gérant, contrôle mobile money séparé
- Dépenses : demande → accord → avance → justificatif → régularisation
- Ménage : tâches automatiques, contrôle réception, alerte « prête en moins de 5 min »
- Alertes temps réel (fin de créneau, dépassement, réservation imminente, appels en retard, ménage) + son
- Journal d'audit non modifiable, rapport d'anomalies, rapports de ventes, export CSV
- Portail client public : disponibilités en direct, demande de réservation (anti-abus sans SMS), suivi
- Restauration (`kitchen/`) : carte gérée par le cuisinier/gérant, commande en chambre prise par la
  réception, circuit cuisine Nouvelle → En préparation → Prête, livraison confirmée par la réception ou
  le personnel de ménage, facturation automatique sur la note du séjour (ligne « Restauration »)

## Hors V1 (prévu V2/V3)
Vente comptant restaurant sans chambre (actuellement non facturée), maintenance, stocks, paie,
notifications push, mode hors ligne complet, 2FA, verrouillage après échecs de connexion,
API mobile money, multilingue (malgache/anglais).

## Hébergement
Ce logiciel est une application **Django/SQLite**, incompatible avec Cloudflare Pages/Workers
(qui ne font tourner que du JavaScript/TypeScript). L'hébergement de référence est **PythonAnywhere**
(gratuit, permanent) : https://oasis.pythonanywhere.com — ce dépôt GitHub sert de source de vérité
versionnée ; voir les instructions de mise à jour du serveur plus haut.
