# Conventions du dépôt

- Langue : français (code commenté, README, messages de commit).
- Branches : toute modification passe par une PR depuis une branche `features/<nom-de-la-feature>` vers `main`. Les branches sont supprimées automatiquement après fusion.
- `ugreen` : branche propre au NAS UGREEN, alimentée automatiquement depuis `main`. Ne pas y faire de modifications générales ; seulement des réglages propres au NAS.
- Les fichiers de `config/` contenant des secrets ou des données locales sont ignorés par git et ne doivent jamais être versionnés.
- Versions figées : yt-dlp et discord.py dans `requirements.txt`, Deno dans le `Dockerfile`.
