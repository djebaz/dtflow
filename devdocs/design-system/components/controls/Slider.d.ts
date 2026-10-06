import * as React from "react";

export interface SliderProps {
  min?: number;
  max?: number;
  value?: number;
  disabled?: boolean;
  onChange?: React.ChangeEventHandler<HTMLInputElement>;
  className?: string;
  [key: string]: any;
}
export declare function Slider(props: SliderProps): JSX.Element;
