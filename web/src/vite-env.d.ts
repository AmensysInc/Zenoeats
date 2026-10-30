/// <reference types="vite/client" />

// Vite's client types are pulled in by the reference above rather than through
// the tsconfig "types" array. That array resolves entries as type *packages*
// under node_modules/@types, and vite/client is a path inside the vite package,
// so listing it there makes editors report it as a missing type definition even
// when tsc resolves it.

/**
 * The build-time configuration this app reads.
 *
 * This documents the contract and gives autocomplete. It does NOT make a
 * mistyped name an error: vite/client declares ImportMetaEnv with a
 * [key: string]: any index signature, and this interface merges with that one
 * rather than replacing it, so unknown keys still type-check. Verified by
 * introducing a typo deliberately -- the build stayed green.
 *
 * Both are optional on purpose. Vite substitutes these at build time, and
 * a build that never set them yields undefined, so the code has to handle it.
 */
interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
