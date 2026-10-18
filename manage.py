import argparse
import getpass
import sqlite3
from app import create_user, db

def main():
    p=argparse.ArgumentParser(description='Administration initiale ChadVoice')
    p.add_argument('action',choices=['init','create-user'])
    args=p.parse_args()
    with db() as cx:
        existing=cx.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
    if args.action=='init' and existing:
        print('Un administrateur existe déjà. Utiliser la plateforme pour créer les autres comptes.');return
    print('Création de compte ('+('administrateur' if args.action=='init' else 'contributeur')+')')
    name=input('Nom complet : ').strip()
    email=input('Adresse e-mail : ').strip()
    pw=getpass.getpass('Mot de passe (10 caractères minimum) : ')
    again=getpass.getpass('Confirmer : ')
    if pw!=again:raise SystemExit('Les mots de passe ne correspondent pas.')
    try:
        create_user(email,name,pw,'admin' if args.action=='init' else 'contributor')
    except (ValueError,sqlite3.IntegrityError) as e:
        raise SystemExit('Erreur : '+str(e))
    print('Compte créé. Aucun mot de passe par défaut n’a été utilisé.')

if __name__=='__main__':main()
