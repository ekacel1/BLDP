#!/usr/bin/env python3
"""Repare google-colab-cli 0.6.0, dont une dependance a change de nom.

Le CLI appelle « jupyter_kernel_client.KernelClient », mais la version 1.0.2 de
ce paquet a renomme la classe en « JupyterKernelClient ». Comme le CLI n'epingle
pas sa dependance, pip installe la version recente et le CLI ne demarre plus.

Le correctif accepte les deux noms. La semantique est identique : « _own_kernel »
est pose dans « __init__ » avant que le CLI le remette a False, donc le noyau
n'est pas detruit a la fermeture du client — ce que le CLI cherchait a garantir.

A REAPPLIQUER apres toute mise a jour de google-colab-cli : la mise a jour
reecrit runtime.py et efface ce correctif.

    /opt/colab-cli/bin/python patch_colab_cli.py /opt/colab-cli/lib/python3.12/site-packages/colab_cli
"""

import pathlib
import sys


def patcher(racine: pathlib.Path) -> int:
    fichier = racine / "runtime.py"
    if not fichier.exists():
        print("introuvable :", fichier, file=sys.stderr)
        return 1

    source = fichier.read_text(encoding="utf-8")
    if "_ClientNoyau" in source:
        print("deja corrige :", fichier)
        return 0

    avant_import = "import jupyter_kernel_client\n"
    apres_import = (
        "import jupyter_kernel_client\n"
        "\n"
        "# jupyter-kernel-client 1.0.2 a renomme « KernelClient » en\n"
        "# « JupyterKernelClient ». Le CLI vise l'ancien nom ; on accepte les deux.\n"
        "_ClientNoyau = getattr(\n"
        "    jupyter_kernel_client,\n"
        '    "KernelClient",\n'
        '    getattr(jupyter_kernel_client, "JupyterKernelClient", None),\n'
        ")\n"
    )
    if avant_import not in source:
        print("import introuvable dans", fichier, file=sys.stderr)
        return 1
    source = source.replace(avant_import, apres_import, 1)

    avant_appel = "self._kernel_client = jupyter_kernel_client.KernelClient("
    if avant_appel not in source:
        print("appel introuvable dans", fichier, file=sys.stderr)
        return 1
    source = source.replace(avant_appel, "self._kernel_client = _ClientNoyau(", 1)

    sauvegarde = fichier.with_suffix(".py.origine")
    if not sauvegarde.exists():
        sauvegarde.write_text(fichier.read_text(encoding="utf-8"), encoding="utf-8")
    fichier.write_text(source, encoding="utf-8")
    print("corrige :", fichier, "(sauvegarde :", sauvegarde.name + ")")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    raise SystemExit(patcher(pathlib.Path(sys.argv[1])))
