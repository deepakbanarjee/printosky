import React from 'react';

interface ToolbarProps {
    selectedBlockId: string | null;
    fontFamily: string;
    fontSize: number;
    onFontFamilyChange: (value: string) => void;
    onFontSizeChange: (value: number) => void;
    onDeleteBlock: () => void;
}

const Toolbar: React.FC<ToolbarProps> = ({
    selectedBlockId,
    fontFamily,
    fontSize,
    onFontFamilyChange,
    onFontSizeChange,
    onDeleteBlock,
}) => {
    return (
        <div className="w-80 bg-white border-l border-gray-200 h-full flex flex-col shadow-xl z-20">
            <div className="p-6 border-b border-gray-100 bg-gray-50">
                <h2 className="text-xl font-bold text-gray-800">Editor Tools</h2>
                <p className="text-xs text-gray-500 mt-1 uppercase tracking-wider font-semibold">Properties Panel</p>
            </div>

            <div className="p-6 space-y-8 overflow-y-auto flex-1">
                {/* Selection Status */}
                <div className={`p-4 rounded-xl border ${selectedBlockId ? 'bg-blue-50 border-blue-200' : 'bg-gray-50 border-gray-200 dashed'}`}>
                    <div className="text-xs font-semibold text-gray-500 uppercase tracking-widest mb-1">Status</div>
                    <div className={`text-sm font-medium ${selectedBlockId ? 'text-blue-700' : 'text-gray-400'}`}>
                        {selectedBlockId ? "Block Selected" : "No Selection"}
                    </div>
                </div>

                {/* Typography Tools */}
                <div className="space-y-4">
                    <label className="text-xs font-bold text-gray-400 uppercase tracking-wider">Typography</label>

                    <div className="space-y-3">
                        <div>
                            <span className="text-xs text-gray-500 mb-1 block">Font Family</span>
                            <select
                                value={fontFamily}
                                onChange={(e) => onFontFamilyChange(e.target.value)}
                                className="w-full text-sm border-gray-300 rounded-lg shadow-sm focus:border-blue-500 focus:ring-blue-500 bg-white p-2.5 text-gray-700"
                                disabled={!selectedBlockId}
                            >
                                <option value="Arial, sans-serif">Arial</option>
                                <option value="Times New Roman, serif">Times New Roman</option>
                                <option value="Courier New, monospace">Courier New</option>
                                <option value="system-ui, -apple-system, sans-serif">System UI</option>
                            </select>
                        </div>

                        <div>
                            <span className="text-xs text-gray-500 mb-1 block">Font Size</span>
                            <div className="flex items-center gap-2">
                                <input
                                    type="number"
                                    value={fontSize}
                                    onChange={(e) => onFontSizeChange(Number(e.target.value))}
                                    className="w-full text-sm border-gray-300 rounded-lg shadow-sm focus:border-blue-500 focus:ring-blue-500 bg-white p-2.5 text-gray-700"
                                    disabled={!selectedBlockId}
                                    min="6"
                                    max="120"
                                />
                                <span className="text-sm font-medium text-gray-400">px</span>
                            </div>
                        </div>
                    </div>
                </div>

                <div className="w-full h-px bg-gray-100"></div>

                {/* Actions */}
                <div className="space-y-3">
                    <label className="text-xs font-bold text-gray-400 uppercase tracking-wider">Actions</label>

                    <button
                        onClick={onDeleteBlock}
                        disabled={!selectedBlockId}
                        className="w-full bg-red-50 text-red-600 border border-red-200 px-4 py-2.5 rounded-lg hover:bg-red-100 hover:border-red-300 disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium transition-all flex items-center justify-center gap-2"
                    >
                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
                        Delete Block
                    </button>
                </div>
            </div>
        </div>
    );
};

export default Toolbar;
