# projector.bot

The marketing site for Projector, served at <https://projector.bot>. It is a
Next.js App Router site styled with Tailwind CSS. Every route renders at build
time, and code samples are highlighted at build time with Shiki, so the page
ships no highlighting JavaScript.

## Develop

```sh
npm install
npm run dev
```

Open <http://localhost:3000>.

## Check

```sh
npm run lint
npx tsc --noEmit
npm run build
```

## Deploy

The site is a directory inside the Projector repository. Point the hosting
project's root directory at `web`. The footer reads the CLI and plugin versions
from `../setup.cfg` and `../.claude-plugin/plugin.json` at build time and omits
them when those files are not present.
