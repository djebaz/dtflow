import * as React from "react";

export interface StatusCardProps {
  state?: "neutral" | "unknown" | "ok" | "warning" | "error";
  heading: string;
  glyph?: string;
  minHeight?: number;
  children?: React.ReactNode;
}
export declare function StatusCard(props: StatusCardProps): JSX.Element;
