# Discord music bot pro Raspberry Pi

Bot prijima prikazy primo v textovem kanalu na Discord serveru.
Krome klasickych textovych prikazu umi i slash commandy a jednoduche Discord GUI s tlacitky.

## Co umi

- `!play <odkaz nebo hledany text>`: text vyhleda primarne na YouTube, URL zkusi prehrat z libovolneho webu, ktery podporuje `yt-dlp`, nebo jako primy stream
- `!radio <stream_url> [alias]`: prida internetove radio a volitelne ho ulozi pod aliasem
- `!radio <alias>`: spusti drive ulozene radio podle aliasu
- `!radios`: vypise ulozene radio aliasy
- `!pause`: pozastavi prehravani
- `!resume`: obnovi prehravani
- `!skip`: preskoci aktualni skladbu
- `!stop`: zastavi prehravani a vymaze frontu
- `!queue`: vypise frontu
- `!np`: ukaze, co prave hraje
- `!leave`: odpoji bota z hlasoveho kanalu
- `!panel`: otevre ovladaci panel s tlacitky a vyberem radia

Slash commandy:

- `/play`, `/radio`, `/radios`, `/pause`, `/resume`, `/skip`, `/stop`, `/queue`, `/np`, `/leave`, `/help`, `/panel`

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

Konfiguraci prefixu, timeoutu a `yt-dlp` voleb muzes upravit v `config.json`.
V `command_aliases.json` si muzes nadefinovat vlastni aliasy prikazu, treba ceske varianty nad puvodnimi anglickymi prikazy.
Po prvnim startu se slash commandy automaticky synchronizuji do Discordu.
Pokud chces, aby se slash commandy propsaly rychle primo na konkretni server, vypln v `config.json` pole `slash_command_guild_ids`.

7. Nastav automaticke spousteni pres `systemd` co nejjednoduseji:

```bash
cd /home/pi/diskzokej
chmod +x install_service.sh
./install_service.sh
```

Skript sam:

- zjisti aktualni cestu k projektu
- najde `python3`
- nastavi spravneho uzivatele
- vytvori `/etc/systemd/system/diskzokej.service`
- zapne automaticky start po bootu
- sluzbu rovnou restartuje

Pokud chces, muzes pred spustenim vynutit konkretniho uzivatele nebo Python:

```bash
cd /home/pi/diskzokej
RUN_USER=pi PYTHON_BIN=/usr/bin/python3 ./install_service.sh
```

8. Kontrola a logy:

```bash
sudo systemctl status diskzokej.service
journalctl -u diskzokej.service -f
```

9. Zakladni testy helperu:

```bash
cd /home/pi/diskzokej
python3 -m unittest discover -s tests
```

## Poznamky

- Pokud byl token ulozeny v `README.md` nebo jinem souboru projektu, zneplatni ho v Discord Developer Portalu a vygeneruj novy.
- Bot potrebuje mit pristup do hlasoveho kanalu stejneho serveru, kde prijima prikazy.
- Prehravani vyuziva `yt-dlp` a `ffmpeg`, takze musi byt `ffmpeg` dostupny v `PATH`.
- `yt-dlp` umi krom YouTube i mnoho dalsich webu. Realna podpora zavisi na konkretni sluzbe a na tom, jestli z ni jde ziskat prehratelny stream.
- Nektere sluzby, typicky cast Spotify odkazu, mohou vratit metadata bez primeho audio streamu. V takovem pripade odkaz nemusi jit prehrat, i kdyz ho stranka normalne otevira v prohlizeci.
- U `!radio` zadavej primou URL audio streamu, ne jen domovskou stranku radia.
- Aliasy radii se ukladaji do `radio_aliases.json`, takze zustanou zachovane i po restartu bota.
- Pokud chces, aby token nebyl v shellu ani v service souboru, nech ho v `token.txt` nebo `.env` vedle `diskzokej.py`.
- `command_prefix` v `config.json` muze byt libovolny neprazdny retezec, napr. `!`, `*`, `:` nebo treba `Prosim `.
- `slash_command_guild_ids` v `config.json` je seznam Discord server ID, kam se maji slash commandy synchronizovat okamzite po restartu, napr. `[123456789012345678]`.
- V `command_aliases.json` jsou klice puvodni anglicke prikazy a hodnoty jsou seznamy aliasu. Puvodni anglicke prikazy zustavaji funkcni vzdycky.
- `panel` otevre Discord GUI s tlacitky `Pause`, `Resume`, `Skip`, `Stop`, `Queue`, `Leave` a dropdownem na ulozena radia.
- Panel je ted jeden hlavni zivy message: pri dalsim otevreni nebo akci se puvodni panel prepise, pripadne presune do noveho kanalu, misto aby pribyvaly dalsi stare zpravy.
