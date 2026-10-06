import * as React from "react";

export interface IconProps {
  /** Source icon (grid, list, detail, refresh, document, json, calendar, fullscreen, chevron), code-drawn ("panel", "connection"), or any Lucide name (server, key-round, shield-check, …). */
  name: string;
  /** Pixel height; width follows the artwork. Default 16. */
  size?: number;
  /** Folder of the source SVGs relative to the page. Default "../../assets/icons/". */
  base?: string;
  /** Status dot for the "connection" icon. */
  state?: "unknown" | "ok" | "warning" | "error";
  style?: React.CSSProperties;
  [key: string]: any;
}
export declare function Icon(props: IconProps): JSX.Element;
