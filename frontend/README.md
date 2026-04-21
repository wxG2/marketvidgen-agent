# Vue + TypeScript + Vite

The VidGen frontend is a Vue 3 single-page application built with Vite and Tailwind CSS.

## Development

```bash
npm install
npm run dev
```

The development server proxies `/api`, `/examples`, and `/generated` to the FastAPI backend on `http://localhost:8000`.

## Scripts

- `npm run dev`: start the Vite development server
- `npm run build`: run TypeScript checks and create a production build
- `npm run lint`: lint TypeScript source files
- `npm run preview`: preview the production build
