"""Durcissement de l'envoi de fichiers.

Trois défauts, tous sans erreur visible en usage normal :

- la taille était vérifiée APRÈS avoir chargé tout le fichier en mémoire ;
- le nom fourni par le client était concaténé tel quel dans un chemin disque ;
- le content_type déclaré par le client était pris pour argent comptant.
"""

from __future__ import annotations

from pathlib import PurePath

from app.routers.files import _signature_matches
from app.services.document_service import _safe_storage_name

_BSLASH = chr(92)

_PNG = bytes.fromhex("89504e470d0a1a0a") + b"reste"
_PDF = b"%PDF-1.7 contenu"
_JPEG = bytes.fromhex("ffd8ff") + b"reste"


class TestAssainissementDuNom:
    """Le nom ne doit jamais pouvoir sortir du dossier de stockage.

    Sur Linux la traversée était bloquée par accident (le préfixe UUID empêche le
    premier segment d'être '..', et le noyau exige que chaque répertoire
    intermédiaire existe), mais sur Windows — où les '..' sont résolus
    lexicalement — 'x/../../evil.txt' écrivait bel et bien hors du dossier.
    """

    def test_traversee_relative_est_neutralisee(self):
        assert _safe_storage_name("../../../etc/passwd") == "passwd"

    def test_traversee_qui_echappait_sur_windows(self):
        assert _safe_storage_name("x/../../evil.txt") == "evil.txt"

    def test_chemin_windows_absolu(self):
        nom = "C:" + _BSLASH + "Users" + _BSLASH + "a" + _BSLASH + "cv.pdf"
        assert _safe_storage_name(nom) == "cv.pdf"

    def test_aucun_separateur_ne_subsiste_jamais(self):
        for brut in ("../../x", "a/b/c.pdf", "a" + _BSLASH + "b.pdf", "./.././x"):
            sortie = _safe_storage_name(brut)
            assert "/" not in sortie and _BSLASH not in sortie
            assert PurePath(sortie).name == sortie

    def test_nom_cache_ne_reste_pas_cache(self):
        assert _safe_storage_name(".htaccess") == "htaccess"

    def test_nom_vide_ou_entierement_filtre(self):
        assert _safe_storage_name("") == "upload"
        assert _safe_storage_name("///") == "upload"

    def test_nom_tres_long_est_tronque(self):
        assert len(_safe_storage_name("a" * 500 + ".pdf")) <= 80

    def test_nom_normal_reste_lisible(self):
        """Contre-épreuve : on assainit sans rendre les noms méconnaissables."""
        assert _safe_storage_name("cv.pdf") == "cv.pdf"
        assert _safe_storage_name("rapport_2026-08.pdf") == "rapport_2026-08.pdf"


class TestVerificationDeSignature:
    """Le content_type est déclaré par le client : il ne prouve rien."""

    def test_type_declare_conforme_au_contenu(self):
        assert _signature_matches(_PNG, "image/png") is True
        assert _signature_matches(_PDF, "application/pdf") is True
        assert _signature_matches(_JPEG, "image/jpeg") is True

    def test_contenu_qui_ment_sur_son_type_est_refuse(self):
        assert _signature_matches(_PDF, "image/png") is False
        # En-tete MZ d'un executable Windows, presente comme une image.
        assert _signature_matches(bytes.fromhex("4d5a9000"), "image/jpeg") is False

    def test_webp_reconnu_malgre_la_taille_intercalee(self):
        data = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"reste"
        assert _signature_matches(data, "image/webp") is True
        assert _signature_matches(b"RIFFxxxxAVI ", "image/webp") is False

    def test_type_sans_signature_verifiable_est_accepte(self):
        """On ne rejette que ce qu'on peut réfuter : le texte brut et le CSV
        n'ont pas de signature stable, les refuser produirait des faux positifs."""
        assert _signature_matches(b"bonjour", "text/plain") is True
        assert _signature_matches(b"a,b,c", "text/csv") is True
        assert _signature_matches(b"quoi que ce soit", None) is True
