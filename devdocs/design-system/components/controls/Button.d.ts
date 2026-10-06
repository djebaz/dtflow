import * as React from "react";

export interface ButtonProps {
  variant?: "neutral" | "primary" | "danger" | "navigation" | "mode" | "disclosure";
  /** Checked/selected state (navy selection; accent for disclosure). */
  checked?: boolean;
  disabled?: boolean;
  onClick?: React.MouseEventHandler<HTMLButtonElement>;
  children?: React.ReactNode;
  className?: string;
  [key: string]: any;
}
export declare function Button(props: ButtonProps): JSX.Element;
