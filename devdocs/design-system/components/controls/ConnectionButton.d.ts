import * as React from "react";

export interface ConnectionButtonProps {
  state?: "unknown" | "ok" | "warning" | "error";
  checked?: boolean;
  disabled?: boolean;
  children?: React.ReactNode;
  [key: string]: any;
}
export declare function ConnectionButton(props: ConnectionButtonProps): JSX.Element;
