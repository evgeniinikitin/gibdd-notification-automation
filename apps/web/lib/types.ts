export type User = {
  id: string;
  email: string;
  fullName: string;
  role: "client_admin" | "client_user" | "support_admin" | "admin";
  organizationId: string;
  organizationName: string;
};

export type RegistrationInput = {
  companyName: string;
  fullName: string;
  email: string;
  password: string;
};

export type AuthTokens = {
  accessToken: string;
  refreshToken?: string;
};

export type OnboardingStepId =
  | "company"
  | "signer"
  | "assets"
  | "fleet"
  | "review";

export type OnboardingStatus = {
  completed: boolean;
  completionPercent: number;
  currentStep: OnboardingStepId;
  completedSteps: OnboardingStepId[];
  missingFields: string[];
};

export type Conversation = {
  id: string;
  title: string;
  preview: string;
  updatedAt: string;
  unread?: boolean;
  tripId?: string;
};

export type MessageAttachment = {
  id: string;
  name: string;
  type: string;
  size: number;
  status: "uploading" | "ready" | "processing" | "error";
};

export type ChatMessage = {
  id: string;
  author: "user" | "assistant" | "system";
  body: string;
  createdAt: string;
  attachments?: MessageAttachment[];
  kind?: string;
  metadata?: Record<string, unknown>;
  action?: {
    label: string;
    kind: "primary" | "secondary";
  };
};

export type TripStatus =
  | "draft"
  | "extracting"
  | "clarifying"
  | "validated"
  | "generating_documents"
  | "dry_run_ready"
  | "submitted"
  | "completed"
  | "needs_support";

export type Trip = {
  id: string;
  reference: string;
  customer: string;
  date: string;
  route: string;
  childrenCount: number;
  status: TripStatus;
  progress: number;
  updatedAt: string;
};

export type WorkflowRunStatus =
  | "queued"
  | "extracting"
  | "clarifying"
  | "needs_review"
  | "completed"
  | "config_required"
  | "failed";

export type WorkflowRun = {
  id: string;
  status: WorkflowRunStatus | string;
  revision: number;
  provider?: string;
  model?: string;
  errorCode?: string;
  errorMessage?: string;
  createdAt?: string;
  startedAt?: string;
  finishedAt?: string;
};

export type MissingClarification = {
  field: string;
  label: string;
  question: string;
  suggestedValue?: string;
  reason?: string;
};

export type WorkflowTrip = {
  id: string;
  reference: string;
  conversationId?: string;
  title: string;
  status: string;
  progress: number;
  tripDate?: string;
  startTime?: string;
  endTime?: string;
  routeText?: string;
  customerType?: string;
  customerName?: string;
  customerInn?: string;
  customerAddress?: string;
  passengerCount?: number;
  selectedVehicleId?: string;
  selectedDriverId?: string;
  extractedData: Record<string, unknown>;
  failureReason?: string;
  confirmedAt?: string;
  portalToken?: string;
  submittedAt?: string;
  updatedAt: string;
};

export type ConversationWorkflow = {
  trip: WorkflowTrip;
  run?: WorkflowRun;
  extractedFields: Record<string, unknown>;
  missingClarifications: MissingClarification[];
  uncertainFields: Array<MissingClarification | string | Record<string, unknown>>;
  summary?: string;
  canConfirm: boolean;
  configurationRequired: boolean;
};

export type SubmissionAttempt = {
  id: string;
  attemptNumber: number;
  status: string;
  finalSubmitAttempted: boolean;
  currentUrl?: string;
  diagnosticFileIds: string[];
  errorMessage?: string;
  startedAt: string;
  completedAt?: string;
};

export type TripAutomation = {
  tripId: string;
  jobId?: string;
  status: string;
  payloadId?: string;
  notificationDocxFileId?: string;
  notificationPdfFileId?: string;
  liveSubmission: boolean;
  attemptCount: number;
  finalSubmitAttempted: boolean;
  portalUrl?: string;
  portalToken?: string;
  lastError?: string;
  attempts: SubmissionAttempt[];
};

export type FileEntry = {
  id: string;
  name: string;
  kind: "folder" | "pdf" | "docx" | "xlsx" | "png" | "jpg" | "jpeg" | "html";
  artifactKind?: string;
  tripId?: string;
  size?: number;
  updatedAt: string;
  owner?: string;
  status?: "ready" | "processing" | "error";
  tripReference?: string;
};

export type BalanceSummary = {
  available: number;
  reserved: number;
  total: number;
  transactions: BalanceTransaction[];
};

export type BalanceTransaction = {
  id: string;
  label: string;
  amount: number;
  createdAt: string;
  actor: string;
};

export type CarrierProfile = {
  id?: string;
  legalName: string;
  inn: string;
  legalAddress: string;
  actualAddress: string;
  phone: string;
  email: string;
  directorPosition: string;
  directorFullName: string;
  licenseNumber: string;
  licenseIssuedAt: string;
  updatedAt?: string;
};

export type ProfileAsset = {
  id: string;
  fileId: string;
  kind: "signature" | "stamp";
  createdAt: string;
};

export type Vehicle = {
  id: string;
  makeModel: string;
  registrationPlate: string;
  year: number | null;
  seats: number;
  diagnosticCardNumber: string;
  diagnosticCardValidUntil: string;
  notes: string;
  isActive: boolean;
  updatedAt: string;
};

export type VehicleInput = Omit<Vehicle, "id" | "updatedAt">;

export type Driver = {
  id: string;
  fullName: string;
  birthDate: string;
  phone: string;
  licenseNumber: string;
  licenseCategories: string;
  licenseIssuedAt: string;
  experienceFrom: string;
  notes: string;
  isActive: boolean;
  updatedAt: string;
};

export type DriverInput = Omit<Driver, "id" | "updatedAt">;

export type AdminTenant = {
  id: string;
  name: string;
  isActive: boolean;
  primaryAdminEmail?: string;
  primaryAdminFullName?: string;
  profileCompleted: boolean;
  onboardingCompleted: boolean;
  onboardingCompletionPercent: number;
  tripsCount: number;
  submittedTripsCount: number;
  available: number;
  reserved: number;
  createdAt?: string;
};
