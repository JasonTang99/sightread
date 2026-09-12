interface Props {
  // The camera folder this shot came from; nothing renders without one.
  device: string;
  // EXIF Model, when the pipeline read one. The folder says who handed the
  // files over, which is not the same thing: "google photos" holds six
  // different cameras on the Hoh trip.
  model?: string;
  corner?: string;
}

export function DeviceBadge({ device, model, corner = "bottom-1.5 left-1.5" }: Props) {
  if (!device) return null;
  return (
    <span
      className={`absolute ${corner} z-10 bg-black/55 text-white text-[10px] px-1.5 py-0.5 rounded-full max-w-[70%] truncate select-none`}
      title={model ? `${device} — ${model}` : device}
    >
      {device}
    </span>
  );
}
