# Discord music bot pro Raspberry Pi

Bot prijima prikazy primo v textovem kanalu na Discord serveru.

## Co umi

- `!play <odkaz nebo hledany text>`: prida skladbu z YouTube nebo ji vyhleda podle textu
- `!radio <stream_url> [alias]`: prida internetove radio a volitelne ho ulozi pod aliasem
- `!radio <alias>`: spusti drive ulozene radio podle aliasu
- `!radios`: vypise ulozene radio aliasy
- `!skip`: preskoci aktualni skladbu
- `!stop`: zastavi prehravani a vymaze frontu
- `!queue`: vypise frontu
- `!np`: ukaze, co prave hraje
- `!leave`: odpoji bota z hlasoveho kanalu

## Instalace na Raspberry Pi

1. Nahraj projekt na Raspberry Pi, napriklad do:

```bash
/home/pi/diskzokej
```

2. Nainstaluj systemove balicky:

```bash
sudo apt update
sudo apt install -y python3 python3-pip ffmpeg libffi-dev libnacl-dev
```

3. Nainstaluj Python zavislosti:

```bash
cd /home/pi/diskzokej
python3 -m pip install -r requirements.txt
```

4. V Discord Developer Portalu:

- vytvor aplikaci a bota
- zapni `MESSAGE CONTENT INTENT`
- pozvi bota na server s opravnenimi pro `Send Messages`, `Read Message History`, `Connect`, `Speak`

5. Vloz token jednim z techto zpusobu:

Varianta A: `token.txt` ve stejne slozce jako `diskzokej.py`

```text
sem_vloz_token_bota
```

Varianta B: `.env` ve stejne slozce

```text
DISCORD_TOKEN=sem_vloz_token_bota
```

Varianta C: promenna prostredi

```bash
export DISCORD_TOKEN="sem_vloz_token_bota"
python3 diskzokej.py
```

6. Otestuj rucni spusteni:

```bash
cd /home/pi/diskzokej
python3 diskzokej.py
```

7. Nastav automaticke spousteni pres `systemd`:

Uprav soubor `diskzokej.service`:

- `User=pi` zmen, pokud na Raspberry pouzivas jineho uzivatele
- `WorkingDirectory=/home/pi/diskzokej` uprav podle realne cesty k projektu
- `ExecStart=/usr/bin/python3 /home/pi/diskzokej/diskzokej.py` uprav, pokud mas Python nebo projekt jinde

Pak ho nainstaluj:

```bash
sudo cp /home/pi/diskzokej/diskzokej.service /etc/systemd/system/diskzokej.service
sudo systemctl daemon-reload
sudo systemctl enable diskzokej.service
sudo systemctl start diskzokej.service
```

8. Kontrola a logy:

```bash
sudo systemctl status diskzokej.service
journalctl -u diskzokej.service -f
```

## Poznamky

- Pokud byl token ulozeny v `README.md` nebo jinem souboru projektu, zneplatni ho v Discord Developer Portalu a vygeneruj novy.
- Bot potrebuje mit pristup do hlasoveho kanalu stejneho serveru, kde prijima prikazy.
- Prehravani vyuziva `yt-dlp` a `ffmpeg`, takze musi byt `ffmpeg` dostupny v `PATH`.
- U `!radio` zadavej primou URL audio streamu, ne jen domovskou stranku radia.
- Aliasy radii se ukladaji do `radio_aliases.json`, takze zustanou zachovane i po restartu bota.
- Pokud chces, aby token nebyl v shellu ani v service souboru, nech ho v `token.txt` nebo `.env` vedle `diskzokej.py`.
