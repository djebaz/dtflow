import * as React from "react";

export interface FloatingToolbarProps {
  position?: "top-left" | "top-right" | "bottom-right" | "bottom-center";
  /** Rounded 17px pill (gallery image toolbar) instead of the 6px preview toolbar. */
  pill?: boolean;
  children?: React.ReactNode;
}
export declare function FloatingToolbar(props: FloatingToolbarProps): JSX.Element;
