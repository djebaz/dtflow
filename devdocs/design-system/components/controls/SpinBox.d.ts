import * as React from "react";

export interface SpinBoxProps {
  min?: number;
  max?: number;
  value?: number;
  disabled?: boolean;
  onChange?: React.ChangeEventHandler<HTMLInputElement>;
  className?: string;
  [key: string]: any;
}
export declare function SpinBox(props: SpinBoxProps): JSX.Element;
