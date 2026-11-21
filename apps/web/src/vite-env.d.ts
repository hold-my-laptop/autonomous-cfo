/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL?: string;
  readonly VITE_MOCK?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}

declare module "@samples/*?raw" {
  const content: string;
  export default content;
}
