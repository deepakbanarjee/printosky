import React, { useState, useEffect } from 'react';

export interface BlockData {
    id: string;
    type: 'text' | 'image' | 'redaction';
    content?: string;
    image_data?: string; // base64 for new uploads
    bbox: number[]; // [x0, y0, x1, y1]
    script?: string; // Script type: 'devanagari', 'tamil', 'latin', etc.
    style?: {
        fontSize?: number;
        fontFamily?: string;
        color?: string;
        textAlign?: 'left' | 'center' | 'right';
    };
    mask_background?: boolean;
}

interface EditableBlockProps {
    block: BlockData;
    scale: number;
    isSelected: boolean;
    onSelect: (id: string) => void;
    onChange: (id: string, updates: Partial<BlockData>) => void;
}

const EditableBlock: React.FC<EditableBlockProps> = ({
    block,
    scale,
    isSelected,
    onSelect,
    onChange
}) => {
    const [isDragging, setIsDragging] = useState(false);
    const [isResizing, setIsResizing] = useState(false);
    const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
    const [initialBbox, setInitialBbox] = useState<number[] | null>(null);

    // Current display coordinates (local state for smooth dragging)
    const [localBbox, setLocalBbox] = useState(block.bbox);

    useEffect(() => {
        if (!isDragging && !isResizing) {
            setLocalBbox(block.bbox);
        }
    }, [block.bbox, isDragging, isResizing]);

    const handleMouseDown = (e: React.MouseEvent) => {
        e.stopPropagation();
        onSelect(block.id);
        setIsDragging(true);
        setDragStart({ x: e.clientX, y: e.clientY });
        setInitialBbox([...localBbox]);
    };

    const handleResizeMouseDown = (e: React.MouseEvent) => {
        e.stopPropagation();
        setIsResizing(true);
        setDragStart({ x: e.clientX, y: e.clientY });
        setInitialBbox([...localBbox]);
    };

    useEffect(() => {
        const handleMouseMove = (e: MouseEvent) => {
            if (!isDragging && !isResizing) return;
            if (!initialBbox) return;

            const dx = (e.clientX - dragStart.x) / scale;
            const dy = (e.clientY - dragStart.y) / scale;

            if (isDragging) {
                const [x0, y0, x1, y1] = initialBbox;
                const width = x1 - x0;
                const height = y1 - y0;
                const newX0 = x0 + dx;
                const newY0 = y0 + dy;
                setLocalBbox([newX0, newY0, newX0 + width, newY0 + height]);
            } else if (isResizing) {
                const [x0, y0, x1, y1] = initialBbox;
                // Resize changes x1, y1
                setLocalBbox([x0, y0, x1 + dx, y1 + dy]);
            }
        };

        const handleMouseUp = () => {
            if (isDragging || isResizing) {
                setIsDragging(false);
                setIsResizing(false);
                // Commit changes
                onChange(block.id, { bbox: localBbox });
            }
        };

        if (isDragging || isResizing) {
            window.addEventListener('mousemove', handleMouseMove);
            window.addEventListener('mouseup', handleMouseUp);
        }

        return () => {
            window.removeEventListener('mousemove', handleMouseMove);
            window.removeEventListener('mouseup', handleMouseUp);
        };
    }, [isDragging, isResizing, dragStart, initialBbox, scale, block.id, onChange, localBbox]);

    const [x0, y0, x1, y1] = localBbox;
    const left = x0 * scale;
    const top = y0 * scale;
    const width = (x1 - x0) * scale;
    const height = (y1 - y0) * scale;

    const fontSize = (block.style?.fontSize || 12) * scale;

    // Font selection based on script
    const getFontFamily = () => {
        const script = block.script || 'latin';
        const scriptFontMap: Record<string, string> = {
            'devanagari': 'Noto Sans Devanagari, sans-serif',
            'tamil': 'Noto Sans Tamil, sans-serif',
            'telugu': 'Noto Sans Telugu, sans-serif',
            'bengali': 'Noto Sans Bengali, sans-serif',
            'gujarati': 'Noto Sans Gujarati, sans-serif',
            'kannada': 'Noto Sans Kannada, sans-serif',
            'malayalam': 'Noto Sans Malayalam, sans-serif',
            'gurmukhi': 'Noto Sans Gurmukhi, sans-serif',
            'odia': 'Noto Sans Oriya, sans-serif',
            'latin': block.style?.fontFamily || 'system-ui, sans-serif'
        };
        return scriptFontMap[script] || scriptFontMap['latin'];
    };

    // BACKGROUND / BORDER LOGIC
    // We want to see the text behind if we are "masking" but currently editing (transparency).
    // If not selected, and mask_background is true, we want opaque white (to hide original).
    // If selected, we want semi-transparent white (so we see what we align to).

    let backgroundColor = 'transparent';
    if (block.type === 'redaction') {
        // Redaction block: always white. Semi-transparent if selected to see under it.
        backgroundColor = isSelected ? 'rgba(255, 255, 255, 0.5)' : 'rgba(255, 255, 255, 1)';
    } else {
        // Text/Image block
        if (block.mask_background) {
            // If masking is ON:
            // Selected -> See through (0.85) to align text.
            // Not Selected -> Opaque (1.0) to cover original text.
            backgroundColor = isSelected ? 'rgba(255, 255, 255, 0.85)' : '#FFFFFF';
        } else {
            // Masking OFF: transparent always
            backgroundColor = 'transparent';
        }
    }

    const containerStyle: React.CSSProperties = {
        position: 'absolute',
        left: `${left}px`,
        top: `${top}px`,
        width: `${width}px`,
        height: `${height}px`,
        zIndex: isSelected ? 20 : 10,
        cursor: isDragging ? 'grabbing' : 'grab',
        border: isSelected ? '1px solid #3b82f6' : '1px solid transparent',
        backgroundColor: backgroundColor,
        boxShadow: isSelected ? '0 0 0 1px rgba(59, 130, 246, 0.3)' : 'none',
        transition: 'background-color 0.1s ease-in-out, border-color 0.1s ease-in-out',
    };

    // Hover effect for unselected blocks to hint interactability
    const hoverClass = !isSelected ? "hover:border-blue-300 hover:border-dashed" : "";

    const handleImageUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (file) {
            const reader = new FileReader();
            reader.onloadend = () => {
                const base64String = reader.result as string;
                onChange(block.id, { image_data: base64String });
            };
            reader.readAsDataURL(file);
        }
    };

    return (
        <div
            style={containerStyle}
            className={`group ${hoverClass}`}
            onMouseDown={handleMouseDown}
        >
            {block.type === 'text' && (
                <textarea
                    value={block.content || ''}
                    onChange={(e) => onChange(block.id, { content: e.target.value })}
                    style={{
                        width: '100%',
                        height: '100%',
                        fontSize: `${fontSize}px`,
                        fontFamily: getFontFamily(),
                        resize: 'none',
                        border: 'none',
                        background: 'transparent',
                        padding: 0,
                        margin: 0,
                        overflow: 'hidden',
                        whiteSpace: 'pre-wrap',
                        lineHeight: 1.1, // Tight alignment
                        color: block.style?.color || 'black',
                        outline: 'none',
                        display: 'block'
                    }}
                    className="w-full h-full"
                    spellCheck={false}
                />
            )}

            {block.type === 'image' && (
                <div className="w-full h-full relative group-inner">
                    {block.image_data ? (
                        <img src={block.image_data} className="w-full h-full object-contain" alt="Replacement" />
                    ) : (
                        <div className="w-full h-full flex items-center justify-center bg-gray-100 text-gray-400 text-xs">
                            PDF Image
                        </div>
                    )}

                    {isSelected && (
                        <label className="absolute inset-0 flex items-center justify-center bg-black bg-opacity-50 text-white cursor-pointer opacity-0 group-hover:opacity-100 transition-opacity">
                            <span className="text-sm font-bold">Replace</span>
                            <input type="file" accept="image/*" className="hidden" onChange={handleImageUpload} />
                        </label>
                    )}
                </div>
            )}

            {block.type === 'redaction' && (
                <div className="w-full h-full flex items-center justify-center text-xs text-gray-400 select-none">
                    {isSelected && "Redaction Area"}
                </div>
            )}

            {/* Resize Handle */}
            {isSelected && (
                <div
                    className="absolute bottom-0 right-0 w-4 h-4 bg-blue-500 cursor-nwse-resize z-30 transform translate-x-1/2 translate-y-1/2 rounded-full border border-white"
                    onMouseDown={handleResizeMouseDown}
                />
            )}
        </div>
    );
};

export default EditableBlock;
