/**
 * Brand Profile list items carry the complete bounded draft. With the
 * contract maxima (64 rules, four 64×512 text lists, 32 colors, and
 * 64 asset selections), two worst-case UTF-8 items remain below the
 * gateway's immutable 2 MiB response cap. Three do not.
 */
export const BRAND_PROFILE_SAFE_PAGE_SIZE = 2;
