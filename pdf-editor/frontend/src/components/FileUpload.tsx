import React, { useCallback, useState } from 'react';
import { validateUpload } from '../lib/validateUpload';

interface FileUploadProps {
    onUpload: (file: File) => void;
    isUploading: boolean;
}

const FileUpload: React.FC<FileUploadProps> = ({ onUpload, isUploading }) => {
    const [dragActive, setDragActive] = useState(false);
    const [validationError, setValidationError] = useState<string | null>(null);

    const tryUpload = useCallback((file: File) => {
        const result = validateUpload(file);
        if (!result.ok) {
            setValidationError(result.error);
            return;
        }
        setValidationError(null);
        onUpload(file);
    }, [onUpload]);

    const handleDrag = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        if (e.type === "dragenter" || e.type === "dragover") {
            setDragActive(true);
        } else if (e.type === "dragleave") {
            setDragActive(false);
        }
    }, []);

    const handleDrop = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setDragActive(false);
        if (e.dataTransfer.files && e.dataTransfer.files[0]) {
            tryUpload(e.dataTransfer.files[0]);
        }
    }, [tryUpload]);

    const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        e.preventDefault();
        if (e.target.files && e.target.files[0]) {
            tryUpload(e.target.files[0]);
        }
    };

    return (
        <div className="flex flex-col items-center justify-center min-h-[50vh] w-full max-w-2xl mx-auto p-8">
            <div
                className={`w-full relative flex flex-col items-center justify-center p-12 border-2 border-dashed rounded-3xl transition-all duration-300 ease-in-out
          ${dragActive
                        ? "border-blue-500 bg-blue-50 bg-opacity-20 scale-105 shadow-xl"
                        : "border-gray-300 bg-white bg-opacity-60 backdrop-blur-md hover:border-blue-400 hover:bg-white hover:bg-opacity-80 shadow-lg"
                    }`}
                onDragEnter={handleDrag}
                onDragLeave={handleDrag}
                onDragOver={handleDrag}
                onDrop={handleDrop}
            >
                <div className="text-center">
                    <svg className="w-16 h-16 mx-auto mb-4 text-blue-500 opacity-80" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" /></svg>
                    <h3 className="mb-2 text-2xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-600 to-purple-600">
                        Upload PDF
                    </h3>
                    <p className="mb-6 text-gray-500">Drag & Drop your PDF here or click to browse</p>
                </div>

                <input
                    id="file-upload"
                    type="file"
                    className="hidden"
                    accept=".pdf"
                    onChange={handleChange}
                    disabled={isUploading}
                />

                <label
                    htmlFor="file-upload"
                    className={`cursor-pointer px-8 py-3 rounded-full text-white font-semibold shadow-lg transition-transform transform active:scale-95
            ${isUploading
                            ? "bg-gray-400 cursor-not-allowed"
                            : "bg-gradient-to-r from-blue-500 to-purple-600 hover:from-blue-600 hover:to-purple-700 hover:shadow-xl"
                        }`}
                >
                    {isUploading ? "Processing..." : "Select File"}
                </label>

                {dragActive && (
                    <div className="absolute inset-0 w-full h-full bg-blue-100 bg-opacity-10 backdrop-blur-sm rounded-3xl pointer-events-none" />
                )}
            </div>
            {validationError && (
                <div role="alert" className="mt-4 p-3 bg-red-50 text-red-700 rounded-lg border border-red-200 text-sm w-full">
                    {validationError}
                </div>
            )}
        </div>
    );
};

export default FileUpload;
