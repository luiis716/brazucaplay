#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Traz o que mudou em https://github.com/skyrisk/brazucaplay sem substituir o addon inteiro.

O botão Sync fork do GitHub troca os zips e apaga o autoplay, a página e os
endereços do luiis716. Este script só copia o arquivo quando a sua cópia ainda
é igual à oficial anterior. O default.py recebe o diff oficial em cima do
código que você já modificou. Se esse diff não encaixar, nada é gravado.
"""

from __future__ import print_function

import base64
import os
import re
import subprocess
import sys
import tempfile
import zlib
import zipfile

UPSTREAM = "https://github.com/skyrisk/brazucaplay.git"
REF = "refs/remotes/skyrisk/master"
MARCADOR = os.path.join("sync", "oficial.rev")
RAIZ = os.path.dirname(os.path.abspath(__file__))


def run(args, cwd=RAIZ, check=True, capture=False):
    resultado = subprocess.run(
        args,
        cwd=cwd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if check and resultado.returncode != 0:
        erro = (resultado.stderr or b"").decode("utf-8", "replace")
        raise SystemExit(erro or ("falhou: %s" % " ".join(args)))
    return resultado


def git_bytes(rev, caminho):
    resultado = run(["git", "show", "%s:%s" % (rev, caminho)], capture=True, check=False)
    if resultado.returncode != 0:
        return None
    return resultado.stdout


def peel(texto):
    for _ in range(4):
        achou = re.search(r"exec\(\(_\)\((b?'.*?')\)\)", texto, re.S)
        if not achou:
            return texto
        blob = eval(achou.group(1))
        texto = zlib.decompress(base64.b64decode(blob[::-1])).decode("utf-8")
    return texto


def encode(texto):
    versao = re.search(r"versao = '([^']+)'", texto)
    marca = versao.group(1) if versao else "sync"

    def camada(conteudo):
        blob = base64.b64encode(zlib.compress(conteudo.encode("utf-8")))[::-1]
        return "exec((_)(%s))" % repr(blob)

    return (
        "#encoded by Kodi\n"
        "#version %s\n"
        "_ = lambda __ : __import__('zlib').decompress(__import__('base64').b64decode(__[::-1]))\n"
        "%s\n"
        "#checkintegrity23022021\n"
        "#checkintegrity08072020\n"
    ) % (marca, camada(camada(texto)))


def aplicar_diff(antigo, novo, nosso):
    if antigo == novo:
        return nosso
    pasta = tempfile.mkdtemp(prefix="brazuca-sync-")
    try:
        antigo_arq = os.path.join(pasta, "antigo.py")
        novo_arq = os.path.join(pasta, "novo.py")
        nosso_arq = os.path.join(pasta, "nosso.py")
        diff_arq = os.path.join(pasta, "oficial.diff")
        for caminho, conteudo in ((antigo_arq, antigo), (novo_arq, novo), (nosso_arq, nosso)):
            with open(caminho, "w", encoding="utf-8") as arquivo:
                arquivo.write(conteudo)
        with open(diff_arq, "w", encoding="utf-8") as arquivo:
            diff = subprocess.run(
                ["diff", "-u", antigo_arq, novo_arq],
                stdout=arquivo,
            )
        if diff.returncode == 0:
            return nosso
        if diff.returncode != 1:
            return None
        aplicado = subprocess.run(
            ["patch", "--forward", "--batch", "--silent", nosso_arq, diff_arq],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if aplicado.returncode != 0:
            return None
        with open(nosso_arq, "r", encoding="utf-8") as arquivo:
            return arquivo.read()
    finally:
        for nome in os.listdir(pasta):
            os.remove(os.path.join(pasta, nome))
        os.rmdir(pasta)


def ler_zip(dados):
    if dados is None:
        return {}
    import io
    with zipfile.ZipFile(io.BytesIO(dados)) as arquivo:
        return {item.filename: arquivo.read(item.filename) for item in arquivo.infolist()}


def gravar_zip(caminho, membros, origem):
    with zipfile.ZipFile(origem, "r") as antigo, zipfile.ZipFile(caminho, "w", compression=zipfile.ZIP_DEFLATED) as novo:
        nomes = [item.filename for item in antigo.infolist()]
        for nome in nomes:
            if nome not in membros:
                continue
            info = antigo.getinfo(nome)
            item = zipfile.ZipInfo(filename=nome, date_time=info.date_time)
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = info.external_attr
            novo.writestr(item, membros[nome])
        for nome, dados in membros.items():
            if nome in nomes:
                continue
            novo.writestr(nome, dados)


def sincronizar_zip(relativo, antigo_commit, novo_commit, relatorio):
    nosso_caminho = os.path.join(RAIZ, relativo)
    if not os.path.isfile(nosso_caminho):
        relatorio.append("ignorado, zip ausente: %s" % relativo)
        return True
    antigo = ler_zip(git_bytes(antigo_commit, relativo))
    novo = ler_zip(git_bytes(novo_commit, relativo))
    with open(nosso_caminho, "rb") as arquivo:
        nosso = ler_zip(arquivo.read())
    mudou = False
    for nome in sorted(set(antigo) | set(novo)):
        antes = antigo.get(nome)
        depois = novo.get(nome)
        if antes == depois:
            continue
        atual = nosso.get(nome)
        if nome.endswith("default.py") and antes and depois and atual:
            fonte = aplicar_diff(peel(antes.decode("utf-8")), peel(depois.decode("utf-8")), peel(atual.decode("utf-8")))
            if fonte is None:
                print("O default.py oficial mudou num ponto que você também alterou.")
                print("Nada foi gravado. O marcador continua em %s." % antigo_commit)
                return False
            nosso[nome] = encode(fonte).encode("utf-8")
            relatorio.append("patch em %s:%s" % (relativo, nome))
            mudou = True
            continue
        if atual == antes:
            if depois is None:
                nosso.pop(nome, None)
                relatorio.append("removido %s:%s" % (relativo, nome))
            else:
                nosso[nome] = depois
                relatorio.append("copiado %s:%s" % (relativo, nome))
            mudou = True
            continue
        relatorio.append("mantido o seu %s:%s" % (relativo, nome))
    if mudou:
        temporario = nosso_caminho + ".tmp"
        gravar_zip(temporario, nosso, nosso_caminho)
        os.replace(temporario, nosso_caminho)
        copiar_zip_do_repositorio(relativo, nosso_caminho, relatorio)
    return True


def copiar_zip_do_repositorio(relativo, origem, relatorio):
    nome = os.path.basename(relativo)
    addon = nome[:-4]
    pasta = os.path.join(RAIZ, "addons", "repo", "Plugins", addon)
    if not os.path.isdir(pasta):
        return
    with open(origem, "rb") as arquivo:
        dados = arquivo.read()
    for item in os.listdir(pasta):
        if item.startswith(addon + "-") and item.endswith(".zip"):
            destino = os.path.join(pasta, item)
            with open(destino, "wb") as arquivo:
                arquivo.write(dados)
            relatorio.append("repositório atualizado: %s" % os.path.relpath(destino, RAIZ))


def sincronizar_arquivo(relativo, antigo_commit, novo_commit, relatorio):
    antes = git_bytes(antigo_commit, relativo)
    depois = git_bytes(novo_commit, relativo)
    if antes == depois:
        return
    caminho = os.path.join(RAIZ, relativo)
    atual = None
    if os.path.isfile(caminho):
        with open(caminho, "rb") as arquivo:
            atual = arquivo.read()
    if atual != antes:
        relatorio.append("mantido o seu %s" % relativo)
        return
    if depois is None:
        if os.path.isfile(caminho):
            os.remove(caminho)
        relatorio.append("removido %s" % relativo)
        return
    pasta = os.path.dirname(caminho)
    if pasta and not os.path.isdir(pasta):
        os.makedirs(pasta)
    with open(caminho, "wb") as arquivo:
        arquivo.write(depois)
    relatorio.append("copiado %s" % relativo)


def principal():
    os.chdir(RAIZ)
    print("Buscando o oficial...")
    run(["git", "fetch", UPSTREAM, "+master:%s" % REF])
    novo = run(["git", "rev-parse", REF], capture=True).stdout.decode().strip()
    if not os.path.isfile(MARCADOR):
        raise SystemExit("Falta %s. Não vou adivinhar até onde o oficial já foi aplicado." % MARCADOR)
    with open(MARCADOR, "r", encoding="utf-8") as arquivo:
        antigo = arquivo.read().strip()
    if antigo == novo:
        print("Seu fork já está em dia com o oficial %s." % novo[:12])
        return
    if run(["git", "merge-base", "--is-ancestor", antigo, novo], check=False).returncode != 0:
        raise SystemExit("O marcador %s não faz parte do histórico oficial. Nada foi alterado." % antigo[:12])
    nomes = run(["git", "diff", "--name-only", antigo, novo], capture=True).stdout.decode().splitlines()
    relatorio = []
    for relativo in nomes:
        if relativo.endswith(".zip") and relativo.startswith("addons/plugin.video.BrazucaPlay"):
            continue
        sincronizar_arquivo(relativo, antigo, novo, relatorio)
    for relativo in (
        "addons/plugin.video.BrazucaPlay.zip",
        "addons/plugin.video.BrazucaPlay.Matrix.zip",
    ):
        if not sincronizar_zip(relativo, antigo, novo, relatorio):
            return
    with open(MARCADOR, "w", encoding="utf-8") as arquivo:
        arquivo.write(novo + "\n")
    if not relatorio:
        relatorio.append("nenhum arquivo seu precisou mudar")
    print("Oficial avançou de %s para %s." % (antigo[:12], novo[:12]))
    for linha in relatorio:
        print("- %s" % linha)
    print("Revise as mudanças e faça commit quando quiser publicar.")


if __name__ == "__main__":
    try:
        principal()
    except KeyboardInterrupt:
        sys.exit(1)
