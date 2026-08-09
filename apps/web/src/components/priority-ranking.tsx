"use client";

import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { ChevronDown, GripVertical, Plus, X } from "lucide-react";
import { useId, useState } from "react";

export type PriorityOption = {
  value: string;
  label: string;
};

type PriorityRankingProps = {
  priorities: string[];
  options: readonly PriorityOption[];
  onReplace: (index: number, value: string) => void;
  onRemove: (index: number) => void;
  onReorder: (priorities: string[]) => void;
  onAdd: (value: string) => void;
  maxPriorities?: number;
};

function PrioritySelect({
  id,
  label,
  value,
  options,
  onChange,
  placeholder,
}: {
  id: string;
  label: string;
  value: string;
  options: PriorityOption[];
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="nh-priority-select-wrap">
      <select
        id={id}
        aria-label={label}
        className="nh-priority-select"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {placeholder ? (
          <option value="" disabled>
            {placeholder}
          </option>
        ) : null}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <ChevronDown
        aria-hidden="true"
        className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
      />
    </div>
  );
}

function SortablePriorityRow({
  id,
  index,
  priority,
  rowOptions,
  label,
  selectId,
  canRemove,
  onReplace,
  onRemove,
}: {
  id: string;
  index: number;
  priority: string;
  rowOptions: PriorityOption[];
  label: string;
  selectId: string;
  canRemove: boolean;
  onReplace: (value: string) => void;
  onRemove: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  return (
    <li
      ref={setNodeRef}
      style={style}
      className={`flex items-center gap-2 rounded-lg border bg-white px-2 py-2 transition-shadow ${
        isDragging
          ? "z-10 border-teal-200 shadow-md ring-2 ring-teal-600/15"
          : "border-slate-200/80 shadow-sm"
      }`}
    >
      <button
        type="button"
        className="nh-ghost-icon-button cursor-grab touch-none active:cursor-grabbing"
        aria-label={`Drag to reorder ${label}`}
        title="Drag to reorder"
        {...attributes}
        {...listeners}
      >
        <GripVertical className="h-4 w-4" aria-hidden="true" />
      </button>

      <span
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-teal-700 text-xs font-semibold leading-none text-white"
        aria-label={`Priority ${index + 1}`}
      >
        {index + 1}
      </span>

      <PrioritySelect
        id={selectId}
        label={`Priority ${index + 1} factor`}
        value={priority}
        options={rowOptions}
        onChange={onReplace}
      />

      <button
        type="button"
        className="nh-ghost-icon-button text-slate-400 hover:text-red-600"
        onClick={onRemove}
        disabled={!canRemove}
        aria-label={`Remove ${label}`}
        title="Remove priority"
      >
        <X className="h-4 w-4" aria-hidden="true" />
      </button>
    </li>
  );
}

export function PriorityRanking({
  priorities,
  options,
  onReplace,
  onRemove,
  onReorder,
  onAdd,
  maxPriorities = 3,
}: PriorityRankingProps) {
  const baseId = useId();
  const [addPickerOpen, setAddPickerOpen] = useState(false);
  const remainingSlots = maxPriorities - priorities.length;
  const availableOptions = options.filter((option) => !priorities.includes(option.value));
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  function handleAdd(value: string) {
    if (!value) return;
    onAdd(value);
    setAddPickerOpen(false);
  }

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    const oldIndex = priorities.indexOf(String(active.id));
    const newIndex = priorities.indexOf(String(over.id));
    if (oldIndex === -1 || newIndex === -1) return;

    onReorder(arrayMove(priorities, oldIndex, newIndex));
  }

  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-slate-50/50 p-2 shadow-sm">
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={priorities} strategy={verticalListSortingStrategy}>
          <ol className="space-y-2" aria-label="Decision priorities">
            {priorities.map((priority, index) => {
              const rowOptions = options.filter(
                (option) => option.value === priority || !priorities.includes(option.value),
              );
              const label = options.find((option) => option.value === priority)?.label ?? priority;

              return (
                <SortablePriorityRow
                  key={priority}
                  id={priority}
                  index={index}
                  priority={priority}
                  rowOptions={rowOptions}
                  label={label}
                  selectId={`${baseId}-priority-${index}`}
                  canRemove={priorities.length > 1}
                  onReplace={(value) => onReplace(index, value)}
                  onRemove={() => onRemove(index)}
                />
              );
            })}
          </ol>
        </SortableContext>
      </DndContext>

      {remainingSlots > 0 ? (
        <div className="mt-2 px-0.5">
          {addPickerOpen ? (
            <div className="flex items-center gap-2 rounded-lg border border-dashed border-slate-300 bg-white px-2 py-2">
              <PrioritySelect
                id={`${baseId}-add-priority`}
                label="Choose a priority to add"
                value=""
                options={availableOptions}
                placeholder="Choose a factor…"
                onChange={handleAdd}
              />
              <button
                type="button"
                className="nh-ghost-icon-button shrink-0"
                onClick={() => setAddPickerOpen(false)}
                aria-label="Cancel adding priority"
                title="Cancel"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          ) : (
            <button
              type="button"
              className="inline-flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-slate-300 bg-white px-3 py-2.5 text-sm font-medium text-slate-600 transition hover:border-teal-400 hover:bg-teal-50/40 hover:text-teal-800 focus:outline-none focus:ring-2 focus:ring-teal-600/20"
              onClick={() => setAddPickerOpen(true)}
            >
              <Plus className="h-4 w-4 shrink-0" aria-hidden="true" />
              Add a priority ({remainingSlots} available)
            </button>
          )}
        </div>
      ) : (
        <p className="mt-2 px-1 text-xs text-slate-500">
          You can rank up to three priorities. Remove one to choose another.
        </p>
      )}
    </div>
  );
}
