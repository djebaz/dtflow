import * as React from "react";

export interface RoundButtonProps {
  direction?: "prev" | "next";
  onClick?: React.MouseEventHandler<HTMLButtonElement>;
  [key: string]: any;
}
export declare function RoundButton(props: RoundButtonProps): JSX.Element;
